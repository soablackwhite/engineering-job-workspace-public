"""Build an explicit private Obsidian index; never writes CRM or grants submission."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from workflow.evidence import EvidenceIndex, current, facts, digest, sources


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    path.chmod(0o600)


def build(config):
    """Return index and body-free audit; provenance must match selected sources."""
    content = sources(config['vault'], config['paths'])
    for item in config.get('artifact_provenance', []):
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Approved artifact bytes changed; review required')
    for item in config.get('provenance', []):
        text = content.get(item['source'])
        if text is None or digest(text) != item['source_sha256'] or item['quote'] not in text:
            raise ValueError('Source provenance changed or is unavailable; review fact note')
    indexer = EvidenceIndex(config['cache'])
    try:
        index = indexer.build(config['vault'], config['paths'])
    finally:
        indexer.close()
    known = facts(index)
    for item in config.get('provenance', []):
        fact = known.get(item['key'])
        if not fact or fact['conflict'] or fact['value'] != item['value']:
            raise ValueError('Reviewed fact does not match typed index')
    audit = dict(fingerprint=index['fingerprint'], source_count=len(index['paths']),
                 record_count=len(index['records']), fact_count=len(known),
                 cache_hits=index.pop('cache_hits'), cache_misses=index.pop('cache_misses'),
                 integrity=current(index), conflicts=sorted(k for k,v in known.items() if v['conflict']),
                 missing_required=sorted(k for k in config.get('required_facts', []) if k not in known),
                 index_bytes=len(json.dumps(index, ensure_ascii=False).encode()),
                 source_bytes=sum(len(v.encode()) for v in content.values()),
                 cache_bytes=Path(config['cache']).stat().st_size,
                 paths=index['paths'])
    if not audit['integrity']:
        raise ValueError('Evidence changed during build')
    return index, audit


def candidate_merge_proposal(candidate, prepared):
    """Minimal reviewed merge; never clear unrelated source questions or approve review."""
    import copy
    from workflow.engine import sha
    from workflow.evidence import fact_value
    patch=copy.deepcopy(prepared['candidate_patch'])
    index=patch['evidence_index'];resume=prepared['approved_resume']
    approved_hash=resume['sha256']
    if (hashlib.sha256(Path(resume['local_path']).read_bytes()).hexdigest()!=approved_hash
        or fact_value(index,'document.master.sha256')!=approved_hash):
        raise ValueError('Approved attachment and evidence differ')
    removable=set()
    if type(fact_value(index,'professional_years')) in (int,float) and fact_value(index,'professional_years')>=0: removable.add('engineering_experience')
    if fact_value(index,'authorization.US') is False: removable.add('us_work_authorization')
    unresolved=[copy.deepcopy(row) for row in candidate.get('needs_input',[])
                if row.get('field') not in removable]
    doc=dict(variant='master',writer='user-approved-fixed-attachment',pdf_sha256=approved_hash,
             pdf_path=resume['local_path'],filename=resume['filename'],dynamic_tailoring=False,
             claims=[],held=True,hold_reasons=copy.deepcopy(unresolved),review=None,
             source_approval=copy.deepcopy(resume),
             limitations=['Independent current document review remains required; approval to use attachment is not submission permission.'])
    doc_id=sha(doc);doc['id']=doc_id
    answers=copy.deepcopy(candidate.get('application_answers',{}));answers.update(patch['application_answers'])
    base=list(candidate.get('base_documents',[]))
    if doc_id not in base:base.append(doc_id)
    patch.update(application_answers=answers,needs_input=unresolved,
                 master_pdf_sha256=approved_hash,master_document_id=doc_id,base_documents=base)
    return dict(candidate_patch=patch,documents_patch={doc_id:doc},
                resolved_fields=sorted(removable & {r.get('field') for r in candidate.get('needs_input',[])}),
                preserved_blockers=[r.get('field') for r in unresolved],
                preserved_document_ids=list(candidate.get('base_documents',[])),
                old_master_document_id=candidate.get('master_document_id'),
                old_master_pdf_sha256=candidate.get('master_pdf_sha256'),
                submission_ready=False,
                blockers=['Unresolved facts remain recorded and hold packages that depend on them; independent current document review required'] if unresolved else
                         ['Independent current document review required'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    index, audit = build(json.loads(args.config.read_text()))
    private_json(args.output, index)
    private_json(args.output.with_suffix('.audit.json'), audit)
    print(json.dumps(audit, ensure_ascii=False))

if __name__ == '__main__':
    main()
