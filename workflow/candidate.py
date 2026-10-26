"""Import explicitly selected private evidence without inventing candidate facts."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

def build(repository: Path, paths=None, *, source_label=None):
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p for p in paths):
        raise ValueError('Explicit private candidate source paths are required')
    repository = Path(repository).resolve()
    commit = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
    if subprocess.check_output(['git', '-C', str(repository), 'status', '--porcelain'], text=True):
        raise ValueError('Source checkout must be clean before evidence is pinned')
    records = []
    for filename in paths:
        path = (repository / filename).resolve()
        if not path.is_relative_to(repository) or path == repository:
            raise ValueError('Candidate source paths must remain inside the selected repository')
        content = path.read_text()
        chunks = re.split(r'(?=^#{1,3} )', content, flags=re.M) if path.suffix == '.md' else [content]
        offset = 1
        for chunk in chunks:
            if chunk.strip():
                records.append(dict(id='ev-'+digest(filename+'\n'+chunk)[:20], source=filename,
                    commit=commit, line=offset, sha256=digest(chunk), text=chunk,
                    verification='candidate_supplied_unverified'))
            offset += chunk.count('\n')
    return dict(schema_version=1, repository=source_label or 'explicit private source checkout',
        commit=commit, evidence=records, skills={}, variants={},
        constraints=['Source text alone does not establish independently verified qualifications.',
                     'Use reviewed scoped facts; missing answers remain unknown.',
                     'Importing sources grants no browser, deployment or submission permission.'],
        needs_input=[dict(field='reviewed_candidate_facts', reason='Import a reviewed private fact index before qualification.')])

def eligible(job, candidate):
    return dict(decision='needs_input', failures=[], questions=['evidence_gated_screening_required'])

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('repository', type=Path);parser.add_argument('output', type=Path)
    parser.add_argument('--source', action='append', required=True, dest='paths')
    parser.add_argument('--source-label')
    args=parser.parse_args();result=build(args.repository,args.paths,source_label=args.source_label)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');args.output.chmod(0o600)
    print(json.dumps(dict(commit=result['commit'], evidence_count=len(result['evidence']), needs_input_count=len(result['needs_input']))))
