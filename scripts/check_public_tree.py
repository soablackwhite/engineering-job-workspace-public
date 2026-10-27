#!/usr/bin/env python3
"""Generic public-source privacy checks; never print matched secret values."""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {'.private', '.openai', '.obsidian', 'runtime', 'output', 'outputs',
                'screenshots', 'captures', 'sessions', 'backups', 'downloads'}
SKIP_LOCAL_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__',
                   'dist', '.wrangler', '.vinext', '.next', '.pytest_cache'}
PRIVATE_EXTENSIONS = {'.pdf', '.tex', '.docx', '.xlsx', '.eml', '.mbox', '.har',
                      '.pem', '.key', '.p12', '.pfx', '.db', '.cookies'}
RESERVED_DOMAINS = {'example.com', 'example.org', 'example.net', 'example.test',
                    'example.invalid', 'localhost', 'test.invalid'}
# Public automated sender identities are protocol constants, not operator accounts.
PUBLIC_SENDER_DOMAINS = {'linkedin.com', 'ashbyhq.com', 'greenhouse.io',
                         'lever.co', 'myworkday.com', 'workday.com'}
SIGNATURES = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'provider_key': re.compile(r'\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}|AIza[0-9A-Za-z_-]{30,}|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{30,})'),
    'aws_access_id': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'slack_token': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{20,}'),
    'jwt': re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'),
    'credential_url': re.compile(r'''(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s/@:'"<>]+:[^\s/@'"<>]{5,}@'''),
    'machine_home_path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+(?:/|\b)|[A-Za-z]:\\Users\\[^\\\s]+'),
    'personal_hosted_service': re.compile(r'https://[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.chatgpt\.site\b'),
}
EMAIL = re.compile(r'(?<![A-Za-z0-9_.+-])[A-Za-z0-9_.+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9.-])')
SECRET_ASSIGNMENT = re.compile(r'''(?i)(?:api_key|apikey|client_secret|access_token|auth_token|password|passwd)["']?\s*[:=]\s*(["'])([A-Za-z0-9_+/.=-]{16,})\1''')


def path_findings(path: str) -> list[str]:
    p = pathlib.PurePosixPath(path)
    found = []
    if any(part in PRIVATE_DIRS for part in p.parts):
        found.append('private_data_directory')
    if any(part in SKIP_LOCAL_DIRS for part in p.parts):
        found.append('generated_dependency_or_runtime')
    if p.suffix.lower() in PRIVATE_EXTENSIONS or '.sqlite' in p.name:
        found.append('private_document_or_export')
    if p.name.startswith('.env') and p.name != '.env.example':
        found.append('private_environment')
    if p.name.startswith('.dev.vars') and not p.name.endswith('.example'):
        found.append('private_environment')
    if p.name.endswith('.tsbuildinfo'):
        found.append('generated_build_metadata')
    if p.name in {'id_rsa', 'id_ed25519', 'master-manifest.json'}:
        found.append('private_identity_or_document_manifest')
    if p.name.startswith('wrangler.local.') or p.name.endswith(('.local.json', '.local.toml')):
        found.append('local_installation_config')
    return found


def content_findings(data: bytes) -> list[tuple[str, int]]:
    text = data.decode('utf-8', errors='replace')
    found = []
    for category, pattern in SIGNATURES.items():
        found.extend((category, text.count('\n', 0, m.start()) + 1) for m in pattern.finditer(text))
    for m in EMAIL.finditer(text):
        domain = m.group(1).lower()
        local = m.group().split('@', 1)[0].lower()
        reserved = domain in RESERVED_DOMAINS or domain.endswith(('.example', '.test', '.invalid'))
        public_sender = domain in PUBLIC_SENDER_DOMAINS and local in {'noreply', 'no-reply', 'jobs-noreply', 'notification'}
        if not reserved and not public_sender:
            found.append(('non_example_mailbox', text.count('\n', 0, m.start()) + 1))
    for m in SECRET_ASSIGNMENT.finditer(text):
        value = m.group(2).lower()
        if not any(marker in value for marker in ('example', 'dummy', 'fake', 'fixture', 'test', 'placeholder', 'invalid')):
            found.append(('literal_secret_candidate', text.count('\n', 0, m.start()) + 1))
    return sorted(set(found))


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(ROOT), *args], stderr=subprocess.DEVNULL)


def records(staged: bool = False, history: bool = False):
    try:
        git('rev-parse', '--git-dir')
    except subprocess.CalledProcessError:
        if staged or history:
            raise RuntimeError('Staged/history checks require an initialized Git repository.')
        for p in sorted(ROOT.rglob('*')):
            rel = p.relative_to(ROOT)
            if p.is_file() and not any(x in SKIP_LOCAL_DIRS for x in rel.parts):
                yield str(rel), p.read_bytes()
        return
    if history:
        seen = set()
        for commit in git('rev-list', '--all').decode().splitlines():
            for entry in git('ls-tree', '-rz', commit).split(b'\0'):
                if not entry:
                    continue
                meta, path = entry.split(b'\t', 1)
                _, kind, oid = meta.decode().split()
                pair = (path, oid)
                if kind == 'blob' and pair not in seen:
                    seen.add(pair)
                    yield path.decode(), git('cat-file', 'blob', oid)
        return
    paths = git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z') if staged else git('ls-files', '-z')
    for raw in paths.split(b'\0'):
        if not raw:
            continue
        path = raw.decode()
        if staged:
            yield path, git('show', ':' + path)
        else:
            target = ROOT / path
            if target.is_file():
                yield path, target.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--staged', action='store_true')
    scope.add_argument('--history', action='store_true')
    args = parser.parse_args()
    findings, count = [], 0
    try:
        for path, data in records(args.staged, args.history):
            count += 1
            findings.extend((path, category, 0) for category in path_findings(path))
            findings.extend((path, category, line) for category, line in content_findings(data))
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for path, category, line in sorted(set(findings)):
        print(f'{path}:{line}: {category}')
    if findings:
        print('Public-source check failed; inspect privately. Matched values are intentionally omitted.')
        return 1
    print(f'Public-source privacy check passed ({count} file versions). Review personal narratives separately.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
