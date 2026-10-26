"""Content-addressed Obsidian evidence. No model inference or private-vault writes.

A candidate fact is a complete JSON value on a standalone `key: <JSON>` line.
Narrative paragraphs remain retrievable evidence, but never become typed facts
or approved paraphrases automatically. Conflicting facts stay unresolved.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

VERSION = 2

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def signature(manifest):
    return digest(json.dumps(manifest, sort_keys=True))

def sources(vault, paths):
    root = Path(vault).resolve()
    result = {}
    for name in sorted(set(paths)):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path.suffix != '.md':
            raise ValueError('Evidence must be Markdown inside the selected vault')
        result[name] = path.read_text(encoding='utf-8')
    if not result:
        raise ValueError('Select explicit career notes; an empty vault selection is not evidence')
    return result

@lru_cache(maxsize=128)
def _parse(name, text):
    checksum = digest(text); chunks = []; occurrences = {}
    for match in re.finditer(r'\S[\s\S]*?(?=\n\s*\n|\Z)', text):
        quote = match.group().strip()
        occurrences[quote] = occurrences.get(quote, 0) + 1
        row = dict(id='obs-' + signature([name, quote, occurrences[quote]])[:24],
                   source=name, source_sha256=checksum,
                   line=text[:match.start()].count('\n') + 1, text=quote)
        fact = re.fullmatch(r'([a-z][A-Za-z0-9_.-]*): (.+)', quote)
        if fact:
            try: row.update(key=fact[1], value=json.loads(fact[2]))
            except ValueError: pass
        chunks.append(row)
    return chunks


def parse(name, text):
    # Cached parsing, defensive copies; still read/hash source bytes before actions.
    import copy
    return copy.deepcopy(_parse(name, text))


def dependency_snapshot(index, keys=(), ids=()):
    known = facts(index)
    records = {r['id']: r for r in index.get('records', [])}
    return {'facts': {k: known.get(k) for k in sorted(set(keys))},
            'statements': {i: ({k: records[i][k] for k in ('id', 'source', 'text')}
                               if i in records else None) for i in sorted(set(ids))}}


def current(index, keys=None, ids=()):
    try:
        contents = sources(index['vault'], index['paths'])
        manifest = {k: digest(v) for k, v in contents.items()}
        # Validate values and missing/conflicting records too, not merely source IDs.
        records = [row for name, text in contents.items() for row in parse(name, text)]
        if keys is not None:
            return dependency_snapshot(index, keys, ids) == dependency_snapshot({'records': records}, keys, ids)
        return signature(manifest) == index['fingerprint'] and records == index['records']
    except (KeyError, OSError, ValueError):
        return False

class EvidenceIndex:
    def __init__(self, cache):
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache, timeout=20)
        Path(cache).chmod(0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS evidence_cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def build(self, vault, paths):
        manifest, records = {}, []
        hits = misses = 0
        for name, text in sources(vault, paths).items():
            checksum = digest(text); manifest[name] = checksum
            key = signature([VERSION, str(Path(vault).resolve()), name, checksum])
            cached = self.db.execute('SELECT payload FROM evidence_cache WHERE key=?', (key,)).fetchone()
            if cached:
                chunks = json.loads(cached[0]); hits += 1
            else:
                misses += 1; chunks = parse(name, text)
                self.db.execute('INSERT OR REPLACE INTO evidence_cache VALUES (?,?)', (key, json.dumps(chunks)))
            records.extend(chunks)
        self.db.commit()
        return dict(version=VERSION, vault=str(Path(vault).resolve()), paths=sorted(manifest),
                    manifest=manifest, fingerprint=signature(manifest), records=records,
                    cache_hits=hits, cache_misses=misses)

    def close(self): self.db.close()

def facts(index):
    grouped = {}
    for row in index.get('records', []):
        if 'key' in row:
            grouped.setdefault(row['key'], []).append(row)
    return {key: dict(value=rows[0]['value'], evidence_ids=[r['id'] for r in rows],
                      conflict=any(r['value'] != rows[0]['value'] for r in rows))
            for key, rows in grouped.items()}

def fact_value(index, key):
    item = facts(index).get(key, {})
    return None if item.get('conflict') else item.get('value')

def validate_claims(index, claims):
    """Only a whole source statement is auto-verifiable; rewrites need review.

    A valid ID plus an invented technology/metric/scope is a failed claim. This
    deliberately does not pretend lexical similarity is semantic entailment.
    """
    records = {r['id']: r for r in index.get('records', [])}
    issues = []
    if not isinstance(claims, list): return ['malformed_claims']
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict) or not isinstance(claim.get('text'), str):
            issues.append(f'claim_{i}:malformed'); continue
        ids = claim.get('evidence_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(e, str) or e not in records for e in ids):
            issues.append(f'claim_{i}:unknown_evidence'); continue
        if not any(claim['text'].strip() == records[e]['text'] for e in ids) and not rendered_claim_matches(index, claim):
            issues.append(f'claim_{i}:unsupported_or_novel_statement')
        for e in ids:
            row = records[e]
            if row.get('key') and facts(index)[row['key']]['conflict']:
                issues.append(f'claim_{i}:conflicting_evidence')
    return issues


def retrieve(index, evidence_ids, max_chars=12000):
    """Bounded exact-record handoff, with explicit overflow instead of truncation."""
    records = {r['id']: r for r in index.get('records', [])}
    if not isinstance(evidence_ids, list) or any(e not in records for e in evidence_ids):
        raise ValueError('Unknown evidence reference')
    selected = [records[e] for e in dict.fromkeys(evidence_ids)]
    if sum(len(r['text']) for r in selected) > max_chars: raise ValueError('Evidence packet too large; select fewer records')
    return selected


def render_fact_claim(index, key):
    """Small, auditable sentence grammar; no free-form paraphrase approval."""
    import math
    value = fact_value(index, key)
    match = re.fullmatch(r'skill\.([A-Za-z][A-Za-z0-9 +#.-]*)\.years', key)
    if match and type(value) in (int, float) and math.isfinite(value) and value >= 0:
        duration = str(int(value)) if int(value) == value else str(value)
        return f"I have {duration} {'year' if value == 1 else 'years'} of {match[1]} experience."
    raise ValueError('No approved renderer for this fact/scope')


def rendered_claim_matches(index, claim):
    key = claim.get('fact_key')
    if claim.get('template') != 'skill_years_v1' or not isinstance(key, str): return False
    fact = facts(index).get(key)
    if not fact or fact['conflict'] or set(claim['evidence_ids']) != set(fact['evidence_ids']): return False
    try: return claim['text'] == render_fact_claim(index, key)
    except ValueError: return False
