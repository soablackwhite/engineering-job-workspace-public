"""Shared gates for CLI, document workers and guarded browser execution."""
import base64
import copy
import hashlib
import re
from workflow.evidence import current, fact_value, validate_claims
from workflow.qualification import VERSION, screen
from workflow.dependencies import screen_keys, document_dependencies, candidate_binding


def migrate(state):
    """Additive in-memory migration, persisted by the next normal CAS mutation.

    Never rewrite historical statuses, approvals or candidate facts into passes.
    Legacy screens cannot satisfy the new execution gates.
    """
    if state.get('schema_version', 1) > 2: raise ValueError('Unsupported future workflow schema')
    old_version = state.get('schema_version', 1)
    state['schema_version'] = 2
    state.setdefault('artifacts', {})
    state.setdefault('stage_metrics', [])
    if old_version == 2: return state
    for job in state['jobs'].values():
        job.setdefault('funnel_events', [])
        job.setdefault('dimensions', {k: 'unknown' for k in
            ('role_family', 'sponsorship', 'seniority', 'source', 'referral', 'cv_variant', 'submission_mode')})
    return state


def require_screen(state, job):
    from workflow.engine import Held, sha
    from workflow.target_policy import require_target_policy, policy_digest
    require_target_policy(state, job['id'])
    result = job.get('screen') or {}
    policy=state.get('application_selection_policy')
    expected_policy=policy_digest(policy) if isinstance(policy,dict) else None
    if result.get('selection_policy_hash') != expected_policy: raise Held('Application selection policy changed; screen again')
    if result.get('policy_version') != VERSION: raise Held('Legacy screening requires evidence-gated review')
    keys=screen_keys(result.get('facts'))
    if result.get('candidate_hash') != candidate_binding(state['candidate'],keys): raise Held('Candidate facts changed; screen again')
    if result.get('snapshot_hash') != job['snapshots'][-1]['hash']: raise Held('Description changed; screen again')
    index = state['candidate'].get('evidence_index', {})
    if not current(index,keys): raise Held('Obsidian evidence changed or unavailable; refresh and screen again')
    fresh = screen(job, state['candidate'], result['facts'], selection_policy=state.get('application_selection_policy'))
    if fresh['decision'] != 'eligible': raise Held('Eligibility unresolved: ' + ', '.join(fresh['failures'] + fresh['questions']))


def validate_document(candidate, doc):
    """Generated prose must be composed solely of checked whole statements.

    A pinned, source-approved fixed PDF remains unchanged; new PDFs must carry
    exact extracted text matching those statements. No fabricated add-on prose.
    """
    from workflow.engine import Held
    if not isinstance(doc,dict): raise Held('Malformed document output')
    index = candidate.get('evidence_index', {})
    keys,ids=document_dependencies(doc)
    if not current(index,keys,ids): raise Held('Document evidence changed or unavailable')
    if doc.get('candidate_hash') and doc['candidate_hash'] != candidate_binding(candidate,keys,ids): raise Held('Document facts changed')
    issues = validate_claims(index, doc.get('claims', []))
    if issues: raise Held('; '.join(issues))
    approved_hash = fact_value(index, 'document.master.sha256')
    if approved_hash and doc.get('pdf_sha256') == approved_hash:
        if doc.get('variant') != 'master': raise Held('Approved master cannot be relabeled as a tailored CV')
        return
    claims = doc.get('claims')
    if not claims: raise Held('Document needs verified claims or a source-approved fixed master hash')
    expected = '\n\n'.join(c['text'] for c in claims)
    if doc.get('content') != expected: raise Held('Document contains text outside verified claims')
    if not doc.get('pdf_sha256'): raise Held('Exact PDF required')


def verify_pdf(candidate, doc, path):
    from workflow.engine import Held
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != doc.get('pdf_sha256'): raise Held('Document bytes changed')
    validate_document(candidate, doc)
    if doc.get('pdf_sha256') != fact_value(candidate.get('evidence_index', {}), 'document.master.sha256'):
        from pypdf import PdfReader
        text = '\n'.join(p.extract_text() or '' for p in PdfReader(path).pages)
        normalize = lambda v: re.sub(r'\s+', ' ', v).strip()
        if normalize(text) != normalize(doc['content']): raise Held('PDF contains unverified or missing text')
    return raw


def package(state, job, answers, contract=None):
    from workflow.engine import sha, Held
    doc = state['documents'][job['documents'][-1]]
    values = {}
    if not isinstance(answers,dict): raise Held('Malformed answer set')
    for key, value in answers.items():
        known = fact_value(state['candidate'].get('evidence_index', {}), 'answer.' + key)
        if known is None or type(value) is not type(known) or known != value: raise Held('Unknown/conflicting form answer: ' + key)
        values[key] = value
    keys,ids=document_dependencies(doc)
    keys=sorted(set(keys+screen_keys(job['screen']['facts'])+['answer.'+k for k in answers]))
    if not current(state['candidate'].get('evidence_index',{}),keys,ids): raise Held('Package facts changed or unavailable')
    payload = dict(version=VERSION, job_id=job['id'], company=job['company'], title=job['title'],
                   sources=job['sources'], requisition_id=job.get('requisition_id'), snapshot_hash=job['snapshots'][-1]['hash'],
                   document={k: v for k, v in doc.items() if k != 'review'},
                   candidate_hash=candidate_binding(state['candidate'],keys,ids,answers=answers), answers=values, contract=contract or {})
    return dict(sha256=sha(payload), version=VERSION, payload=payload)


def submitted_context(state, job, attempt, raw):
    """Called before browser side effects, so a lost response retains evidence."""
    from workflow.engine import Held
    if len(raw) > 400_000: raise Held('Artifact exceeds inline archive cap; configure private artifact storage before execution')
    state.setdefault('artifacts', {})[attempt['pdf_sha256']] = base64.b64encode(raw).decode('ascii')
    attempt['submission_context'] = dict(dimensions=copy.deepcopy(job['dimensions']),
        eligibility=copy.deepcopy(job['screen']), document=copy.deepcopy(state['documents'][attempt['document_id']]),
        source_urls=copy.deepcopy(job['sources']), artifact_sha256=attempt['pdf_sha256'])


def analytics(state):
    """Actual cohort denominators, unknown history preserved, no causal inference."""
    keys = ('role_family', 'sponsorship', 'seniority', 'source', 'referral', 'cv_variant', 'submission_mode')
    segments = {k: {} for k in keys}
    for job in state['jobs'].values():
        events = list(job.get('funnel_events', []))
        recorded = {e.get('id') for e in events}
        for receipt_key in job.get('reconciliation_events', []):
            receipt = state.get('receipt_events', {}).get(receipt_key, {}).get('event', {})
            if receipt_key not in recorded and receipt.get('status') in ('submitted','interview','offer','rejected','assessment','withdrawn'):
                events.append(dict(id=receipt_key,stage=receipt['status'],at=receipt.get('event_at'),evidence=receipt))
        names = {e['stage'] for e in events}
        attempts = job.get('attempts', [])
        submitted = any(a.get('status') == 'confirmed' and a.get('mode') != 'fixture' for a in attempts) or 'submitted' in names
        if not submitted: continue
        for key in keys:
            submitted_attempt = next((a for a in reversed(attempts) if a.get('status') == 'confirmed' and a.get('mode') != 'fixture'), {})
            submission_event = next((e for e in events if e['stage']=='submitted' and e.get('dimensions')), {})
            dimensions = submitted_attempt.get('submission_context', {}).get('dimensions', submission_event.get('dimensions', {}))
            val = str(dimensions.get(key) or 'unknown')
            bucket = segments[key].setdefault(val, dict(submitted=0, interview=0, offer=0, rejected=0, unknown_outcome=0))
            bucket['submitted'] += 1
            for stage in ('interview', 'offer', 'rejected'): bucket[stage] += stage in names
            bucket['unknown_outcome'] += not bool(names & {'interview', 'offer', 'rejected'})
    for groups in segments.values():
        for bucket in groups.values():
            bucket['interview_rate'] = bucket['interview'] / bucket['submitted']
            bucket['offer_rate'] = bucket['offer'] / bucket['submitted']
    return dict(segments=segments, rejection_cause='unknown_without_explicit_evidence',
                historical_unclassified=sum(not j.get('funnel_events') for j in state['jobs'].values()))
