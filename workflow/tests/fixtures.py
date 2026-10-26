"""Disposable candidate vault and complete independently reviewed job extraction."""
import json
from datetime import datetime, timezone
from workflow.engine import sha
from workflow.evidence import EvidenceIndex


def candidate(root, years=3, pdf_hash='fixture-pdf'):
    vault = root / 'vault'; vault.mkdir(exist_ok=True)
    values = {'professional_years': years, 'authorization.US': False, 'sponsorship_needed.US': True,
              'authorization.GB': False, 'sponsorship_needed.GB': True,
              'residence_country': 'MA', 'relocate.US': True, 'relocate.GB': True,
              'minimum_compensation': {'amount': 110000, 'currency': 'USD', 'period': 'annual'},
              'skill.Python.years': 2, 'skill.PyTorch.years': 0, 'skill.TensorFlow.years': 1,
              'answer.name': 'SYNTHETIC CANDIDATE', 'document.master.sha256': pdf_hash}
    (vault / 'facts.md').write_text('\n\n'.join(k + ': ' + json.dumps(v) for k, v in values.items()))
    indexer = EvidenceIndex(root / 'cache.sqlite')
    try: index = indexer.build(vault, ['facts.md'])
    finally: indexer.close()
    index.pop('cache_hits'); index.pop('cache_misses')
    return dict(verified_engineering_years=years, us_work_authorization=False, evidence_index=index,
                application_answers={'name': {'value': 'SYNTHETIC CANDIDATE', 'confirmed': True, 'source': 'fixture vault'}})


def extraction(job, overrides=None, requirements=None):
    values = dict(role_family='applied_ai', country='US', sponsorship='explicit_yes', seniority='junior',
                  minimum_years=3, location={'mode': 'onsite'},
                  compensation={'maximum': 150000, 'currency': 'USD', 'period': 'annual'})
    values.update(overrides or {})
    snapshot = job['snapshots'][-1]
    observations = {k: {'value': v, 'quote': snapshot['text'], 'source_url': snapshot['source_url'],
                         'checked_at': datetime.now(timezone.utc).isoformat(), 'scope': 'role'} for k,v in values.items()}
    output = dict(extractor='synthetic-extractor', observations=observations, requirements=requirements or [])
    return approve_capture(job, output)


def approve_capture(job, output):
    body = {k:v for k,v in output.items() if k != 'capture_review'}
    output['capture_review'] = dict(snapshot_hash=job['snapshots'][-1]['hash'], extraction_hash=sha(body),
                                    complete=True, reviewer='synthetic-independent-reviewer')
    return output


def reviewed_screen(pipeline, jid, token, output):
    """Explicit synthetic review invocation for existing policy/protocol fixtures."""
    import copy
    job=pipeline.store.read()[1]['jobs'][jid]
    if isinstance(output,dict):
        review=output.get('capture_review',{})
        body={k:v for k,v in output.items() if k!='capture_review'}
        if review.get('extraction_hash')==sha(body) and review.get('snapshot_hash')==job['snapshots'][-1]['hash']:
            pipeline.attest_capture(jid,token,output,copy.deepcopy(output),'fixture-review-adapter')
    return pipeline.screen(jid,token,output)
