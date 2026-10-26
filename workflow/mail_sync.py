"""Receipt reconciliation and an incremental Gmail plan; no email or application sends.

The agent fetches Gmail through the connector, then supplies reviewed normalized
events. This module does not pretend that a browser thread URL is a Gmail API ID.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit, urlunsplit
from workflow.engine import Held, LocalStore, RemoteStore

from workflow.runtime_config import value as configured_value

def receipt_account():
    try:return configured_value('receipt_account', required=True).casefold()
    except ValueError:raise Held('Configure a private receipt mailbox before receipt operations') from None

def supplemental_account():
    return configured_value('supplemental_account')

def configured_accounts():
    return tuple(x for x in (receipt_account(), supplemental_account()) if x)

QUERY_VERSION = 2
PATTERNS = ('in:anywhere -subject:"job alert" '
    '{subject:"application was sent" subject:"thank you for applying" '
    'subject:"thanks for applying" subject:"application received" '
    'subject:"your application" subject:"application update" '
    'subject:"interview" subject:"candidature" '
    'from:jobs-noreply@linkedin.com from:greenhouse.io from:greenhouse-mail.io from:lever.co '
    'from:ashbyhq.com from:myworkday.com from:myworkdayjobs.com '
    'from:workablemail.com from:smartrecruiters.com from:idibu.com from:gem.com from:yello.co}')
LIFECYCLE = {'submitted': 'applied', 'rejected': 'rejected',
             'interview': 'interviewing', 'assessment': 'interviewing',
             'delivery_failed': 'needs_input', 'offer': 'offer', 'withdrawn': 'withdrawn'}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def normalize(value):
    return ' '.join(re.findall(r'[a-z0-9]+', value.casefold()))

def job_url(value):
    p = urlsplit(value)
    if p.scheme != 'https' or not p.hostname:
        raise Held('A verified HTTPS job URL is required')
    if p.hostname == 'linkedin.com' or p.hostname.endswith('.linkedin.com'):
        match = re.search(r'/jobs/view/(?:[^/]*-)?(\d+)', p.path)
        if match:
            return 'https://www.linkedin.com/jobs/view/' + match.group(1)
    # Keep ATS query IDs: unlike tracking parameters, these may identify the role.
    return urlunsplit(('https', p.netloc.lower(), p.path.rstrip('/'), p.query, ''))

def account_state(state, account):
    return state.setdefault('gmail_sync', {}).setdefault(account, {
        'messages': {}, 'last_successful_through': None, 'backfill_complete': False,
        'overlap_hours': 72, 'errors': [], 'pending_run': None})

def plan(state, connected_account, through=None):
    through = through or datetime.now(timezone.utc).isoformat()
    if connected_account.casefold() != receipt_account():
        return {'status': 'blocked_account_mismatch', 'required_account': receipt_account(),
                'connected_account': connected_account, 'checkpoint_advanced': False}
    sync = account_state(state, receipt_account())
    pending = sync.get('pending_run')
    if pending and pending.get('query'):
        return {'status': 'ready', 'account': receipt_account(), 'kind': 'resume_partial',
                'run_id': pending['run_id'], 'scan_kind': pending['kind'],
                'query_version': pending.get('query_version'),
                'through': pending['through'], 'query': pending['query'],
                'next_page_token': pending.get('next_page_token'),
                'remaining_ids': pending.get('remaining_ids', []),
                'checkpoint_basis': sync['last_successful_through'],
                'metadata_first': True, 'batch_read_limit': 100, 'provider_history_api': False}
    query = PATTERNS
    checkpoint = (sync['last_successful_through']
                  if sync.get('query_version') == QUERY_VERSION else None)
    if checkpoint:
        after = datetime.fromisoformat(checkpoint) - timedelta(hours=sync['overlap_hours'])
        query += ' after:' + str(int(after.timestamp()))
    query += ' before:' + str(int(datetime.fromisoformat(through).timestamp()))
    return {'status': 'ready', 'account': receipt_account(), 'through': through,
            'query_version': QUERY_VERSION,
            'kind': 'incremental' if checkpoint else 'initial_targeted_backfill',
            'query': query, 'checkpoint_basis': checkpoint,
            'metadata_first': True, 'batch_read_limit': 100,
            'provider_history_api': False}

def identity_matches(state, event):
    target = job_url(event['job_url']) if event.get('job_url') else None
    employer = normalize(event['company'])
    req = event.get('requisition_id')
    matches = []
    for jid, job in state['jobs'].items():
        urls = {job_url(u) for u in job.get('sources', [])}
        names = {normalize(x) for x in [job['company'], *job.get('employer_aliases', [])]}
        reqs = {str(x) for x in [job.get('requisition_id'), *job.get('requisition_aliases', [])] if x}
        if (target and target in urls) or (req and employer in names and str(req) in reqs):
            matches.append(jid)
    # An explicitly reviewed binding can confirm an existing user-recorded
    # application without inventing a job URL from a receipt that has none.
    if event.get('reviewed_job_id'):
        jid = event['reviewed_job_id']
        job = state['jobs'].get(jid)
        if not job or not event.get('identity_basis'):
            raise Held('Reviewed receipt binding needs an existing job and evidence basis')
        names = {normalize(x) for x in [job['company'], *job.get('employer_aliases', [])]}
        recorded_date = job.get('application', {}).get('application_date')
        if (employer not in names or normalize(event['title']) != normalize(job['title'])
            or not recorded_date or recorded_date != event.get('application_date')
            or any(match != jid for match in matches)):
            raise Held('Reviewed receipt binding conflicts with employer, role, date or identity')
        matches = [jid]
    return matches

def apply_event(state, event):
    """Keep history and identity; a second notification is not a second application."""
    if event['account'] not in configured_accounts():
        raise Held('Receipt account mismatch')
    if event.get('source') == 'gmail_connector':
        if not event.get('message_id') or not event.get('thread_id'):
            raise Held('Stable Gmail message and thread IDs required')
        key = event['account'] + ':' + event['message_id']
    elif event.get('source') == 'gmail_browser' and event.get('evidence_key'):
        key = event['account'] + ':browser:' + event['evidence_key']
    else:
        raise Held('Explicit receipt provenance required')
    ledger = state.setdefault('receipt_events', {})
    if key in ledger:
        if ledger[key]['event'] != event:
            raise Held('Receipt ID changed content; review the existing event')
        return ledger[key]['outcome']
    if event['status'] in ('ignored', 'uncertain'):
        outcome = {'status': event['status'], 'reason': event.get('reason', '')}
    else:
        if event['status'] not in LIFECYCLE or not event.get('excerpt'):
            raise Held('Unsupported lifecycle or missing receipt excerpt')
        matches = identity_matches(state, event)
        if len(matches) > 1:
            outcome = {'status': 'uncertain', 'reason': 'Conflicting job identities', 'jobs': matches}
        elif not matches and not event.get('job_url'):
            outcome = {'status': 'uncertain', 'reason': 'No exact job or requisition identity'}
        else:
            jid = matches[0] if matches else ('linkedin-' + job_url(event['job_url']).rsplit('/', 1)[-1]
                if 'linkedin.com/jobs/view/' in job_url(event['job_url'])
                else 'receipt-job-' + digest(job_url(event['job_url']))[:20])
            if jid not in state['jobs']:
                state['jobs'][jid] = dict(id=jid, company=event['company'], title=event['title'],
                    state='applied', sources=[job_url(event['job_url'])], snapshots=[], attempts=[],
                    documents=[], revisions=0, claim=None, screen=None, tracking={}, possible_duplicates=[],
                    location=event.get('location', ''), requisition_id=event.get('requisition_id'))
            job = state['jobs'][jid]
            # Different URLs can still be reposts. Preserve each event/row and flag
            # similar roles for review instead of silently merging or clearing them.
            words = set(normalize(job['title']).split())
            for other_id, other in state['jobs'].items():
                other_words = set(normalize(other['title']).split())
                names = {normalize(other['company']), *map(normalize, other.get('employer_aliases', []))}
                if (other_id != jid and normalize(job['company']) in names and words and other_words
                    and (words <= other_words or other_words <= words
                         or len(words & other_words) / len(words | other_words) >= .5)):
                    if other_id not in job.setdefault('possible_duplicates', []): job['possible_duplicates'].append(other_id)
                    if jid not in other.setdefault('possible_duplicates', []): other['possible_duplicates'].append(jid)
            job.setdefault('reconciliation_events', []).append(key)
            if event['status'] in ('submitted','interview','assessment','offer','rejected','withdrawn'):
                job.setdefault('funnel_events', []).append({'id':key,'stage':event['status'],
                    'at':event['event_at'],'evidence':{'source':event['source'],'receipt_key':key,'excerpt':event['excerpt']}})
            for alias in event.get('employer_aliases', []):
                if alias not in job.setdefault('employer_aliases', []): job['employer_aliases'].append(alias)
            for req in event.get('requisition_aliases', []):
                if req not in job.setdefault('requisition_aliases', []): job['requisition_aliases'].append(req)
            if event.get('job_url'):
                url = job_url(event['job_url'])
                if url not in job['sources']: job['sources'].append(url)
                alias_key = 'url:' + url
                if state['aliases'].get(alias_key, jid) != jid: raise Held('URL alias conflict')
                state['aliases'][alias_key] = jid
            # Read-receipt time is not represented as the exact application time.
            application = job.setdefault('application', {})
            old_event_at = application.get('lifecycle_event_at', '')
            event_at = event['event_at']
            receipt_would_regress = (event['status'] == 'submitted' and
                (job['state'] in ('rejected', 'interviewing', 'offer', 'withdrawn')
                 or application.get('delivery_status') == 'failed'))
            if event_at >= old_event_at and not receipt_would_regress:
                previous_state = job['state']
                job['state'] = LIFECYCLE[event['status']]
                application.update(status=job['state'].title(), lifecycle_event_at=event_at,
                                   lifecycle_source=key, receipt_verified=True)
                if event['status'] == 'delivery_failed':
                    application.update(status='Delivery issue', delivery_status='failed',
                                       action_required='Review failed ATS delivery; do not automatically resubmit')
                elif event['status'] == 'assessment':
                    application.update(status='Assessment requested',
                                       action_required='Review assessment invitation; do not start automatically')
                if previous_state != job['state']:
                    job.setdefault('reconciliation_state_history', []).append(
                        {'before': previous_state, 'after': job['state'], 'evidence': key})
            if event['status'] == 'submitted':
                application.setdefault('application_date', event['application_date'])
                application['submission_verified_by_email'] = True
            # Existing tracking is immutable at /api/workflow; preserve it exactly.
            outcome = {'status': 'reconciled', 'job_id': jid, 'lifecycle': event['status']}
    ledger[key] = {'event': copy.deepcopy(event), 'outcome': outcome}
    if event.get('source') == 'gmail_connector':
        account_state(state, event['account'])['messages'][event['message_id']] = {
            'thread_id': event['thread_id'], 'outcome': outcome, 'event_key': key}
    return outcome

def apply_bundle(store, bundle):
    """CAS-write receipts, verify readback, only then checkpoint a complete API scan."""
    revision, state = store.read()
    state = copy.deepcopy(state)
    account = bundle['account']
    if account not in configured_accounts():
        raise Held('Wrong application mailbox')
    if any(e.get('account') != account or e.get('source') != bundle.get('source')
           for e in bundle['events']):
        raise Held('Receipt source/account differs from bundle provenance')
    sync = account_state(state, account)
    connector = bundle.get('source') == 'gmail_connector'
    if account == supplemental_account() and connector:
        raise Held('Supplemental mailbox currently supports reviewed browser evidence only')
    run_fields = {k: bundle.get(k) for k in ('query', 'through', 'kind', 'query_version')}
    run_id = bundle.get('run_id') or digest({'account': account, **run_fields})
    pending = sync.get('pending_run') if connector else None
    if connector and (not run_fields['query'] or not run_fields['through']
                      or run_fields['query_version'] != QUERY_VERSION
                      or run_fields['kind'] not in ('initial_targeted_backfill', 'incremental')):
        raise Held('Connector scan needs fixed query, upper bound, kind and current query version')
    if connector and run_fields['kind'] == 'incremental' and (
            not sync['backfill_complete'] or sync.get('query_version') != QUERY_VERSION):
        raise Held('Complete the current query-version backfill before incremental ingestion')
    if pending and (pending['run_id'] != run_id or any(
            pending.get(k) != v for k, v in run_fields.items())):
        raise Held('Resume the unfinished connector scan before starting another interval')
    outcomes = [apply_event(state, event) for event in bundle['events']]
    required_ids = set((pending or {}).get('searched_message_ids', []))
    required_ids.update(bundle.get('searched_message_ids', []))
    if connector:
        required_ids.update(e['message_id'] for e in bundle['events'])
    errors = [e for e in (pending or {}).get('errors', [])
              if e not in bundle.get('resolved_errors', [])]
    errors.extend(e for e in bundle.get('errors', []) if e not in errors)
    next_token = bundle.get('next_page_token', (pending or {}).get('next_page_token'))
    pages_fetched = bundle.get('all_pages_fetched', (pending or {}).get('all_pages_fetched', False))
    complete = (connector and pages_fetched is True and next_token is None
                and not errors and required_ids <= set(sync['messages'])
                and all(o['status'] != 'uncertain' for o in outcomes)
                and all(sync['messages'][i]['outcome']['status'] != 'uncertain' for i in required_ids))
    # A partial/browser scan can persist evidence, but never complete a Gmail backfill.
    if connector:
        sync['pending_run'] = {'run_id': run_id, **run_fields,
            'all_pages_fetched': pages_fetched, 'next_page_token': next_token,
            'searched_message_ids': sorted(required_ids), 'errors': errors,
            'remaining_ids': sorted(required_ids - set(sync['messages'])),
            'uncertain_ids': sorted(i for i in required_ids & set(sync['messages'])
                if sync['messages'][i]['outcome']['status'] == 'uncertain')}
        sync['errors'] = errors
    else:
        sync['last_browser_audit'] = {'run_id': run_id, 'checkpoint_eligible': False}
    state['paused'] = True  # Reconciliation never resumes submissions.
    batch_hash = digest(bundle)
    if batch_hash not in state.setdefault('receipt_batches', []):
        state['receipt_batches'].append(batch_hash)
        state['events'].append({'at': time.time(), 'action': 'receipt_reconciliation', 'run_id': run_id})
    expected = digest({'receipts': state.get('receipt_events'), 'sync': sync,
                       'jobs': state['jobs'], 'aliases': state['aliases']})
    store.save(revision, state)
    verified_revision, verified = store.read()
    actual = digest({'receipts': verified.get('receipt_events'),
                     'sync': verified.get('gmail_sync', {}).get(account),
                     'jobs': verified['jobs'], 'aliases': verified['aliases']})
    if actual != expected: raise Held('Receipt write not verified; checkpoint unchanged')
    if complete:
        v = verified['gmail_sync'][account]
        v['last_successful_through'] = max(v['last_successful_through'] or '', bundle['through'])
        v['backfill_complete'] = v['backfill_complete'] or bundle.get('kind') == 'initial_targeted_backfill'
        v['query_version'] = QUERY_VERSION
        v['pending_run'] = None
        store.save(verified_revision, verified)
        _, check = store.read()
        if check['gmail_sync'][account] != v: raise Held('Checkpoint write not verified')
    return {'outcomes': outcomes, 'checkpoint_advanced': complete, 'queue_paused': True}

def paused_job_authorized(state, jid):
    """A dated, explicit wrap-up grant permits only an already-held draft."""
    grant = state.get('submission_controls', {}).get('draft_wrapup_authorization', {})
    job = state.get('jobs', {}).get(jid, {})
    return bool(grant.get('source') and jid in grant.get('job_ids', [])
                and grant.get('expires_at', 0) > time.time()
                and job.get('draft_progress') and not job.get('application'))

def crm_linkedin_policy(state):
    controls = state.get('submission_controls', {})
    waiver = controls.get('gmail_requirement_waiver', {})
    return (controls.get('receipt_check_policy') == 'crm_linkedin'
            and controls.get('gmail_crosscheck_required') is False
            and waiver.get('source') == 'direct_user_statement'
            and bool(waiver.get('quote')) and bool(waiver.get('at')))

def require_linkedin_check(state, jid, check):
    from workflow.engine import canonical
    job = state['jobs'][jid]
    try:
        exact_url = canonical((check or {}).get('url', ''))
    except (ValueError, TypeError):
        raise Held('Fresh exact LinkedIn application-status check required')
    if (not check or check.get('job_id') != jid
        or check.get('source') != 'linkedin_visible_ui'
        or check.get('application_status') != 'not_applied'
        or not 0 <= time.time() - check.get('checked_at', 0) <= 600
        or exact_url not in job.get('sources', [])):
        raise Held('Fresh exact LinkedIn application-status check required')
    if any(job.get(k) for k in ('holds', 'duplicate_hold', 'application', 'attempts', 'reconciliation_events')):
        raise Held('Existing application evidence or hold requires reconciliation')

def reserve(store, jid, owner, gmail_check=None, duplicate_check=None):
    """Shared employer reservation after an exact and fuzzy historical cross-check."""
    revision, state = store.read()
    from workflow.target_policy import require_target_policy
    require_target_policy(state, jid)
    from workflow.engine import require_application_route
    require_application_route(state, jid)
    controls = state.get('submission_controls', {})
    if (state['paused'] and not paused_job_authorized(state, jid)) or controls.get('audit_status') != 'complete':
        raise Held('Audit/submission pause remains active')
    ui_only = crm_linkedin_policy(state)
    if ui_only:
        require_linkedin_check(state, jid, duplicate_check)
    else:
        gmail_check = gmail_check or {}
        sync = account_state(state, receipt_account())
        per_job = controls.get('receipt_check_policy') == 'per_application' and controls.get('per_application_authorization')
        if not per_job and (not sync['backfill_complete'] or sync.get('query_version') != QUERY_VERSION):
            raise Held('Gmail history reconciliation incomplete')
        if per_job and (gmail_check.get('scope') != 'all_time_employer_role_requisition'
                       or gmail_check.get('profile_verified') is not True
                       or gmail_check.get('source') not in ('gmail_connector', 'gmail_browser')):
            raise Held('Verified all-time per-application receipt check required')
        if (gmail_check.get('job_id') != jid or gmail_check.get('account') != receipt_account() or not gmail_check.get('query')
            or gmail_check.get('all_pages_fetched') is not True or gmail_check.get('clear') is not True
            or not 0 <= time.time() - gmail_check.get('checked_at', 0) <= 600):
            raise Held('Fresh completed Gmail employer/role/requisition check required')
    job = state['jobs'][jid]
    employer = normalize(job['company'])
    names = {employer, *map(normalize, job.get('employer_aliases', []))}
    for entry in state.get('receipt_events', {}).values():
        if entry['outcome']['status'] != 'uncertain': continue
        event = entry['event']
        uncertain_names = {normalize(event.get('company', '')),
                           *map(normalize, event.get('employer_aliases', []))}
        if names & uncertain_names:
            raise Held('Unresolved historical employer evidence requires identity review')
    related = []
    for other_id, other in state['jobs'].items():
        other_names = {normalize(other['company']), *map(normalize, other.get('employer_aliases', []))}
        has_attempt = other.get('attempts') or other.get('application') or other.get('reconciliation_events')
        if names & other_names and has_attempt:
            related.append(other_id)
    if related or job.get('possible_duplicates') or job.get('duplicate_group'):
        raise Held('Prior employer application/repost needs identity review: ' + ', '.join(related))
    reservations = state.setdefault('submission_reservations', {})
    if any(names & set(r['employer_names']) for r in reservations.values()):
        raise Held('Employer already reserved; reconcile before release')
    reservation = {'token': str(uuid.uuid4()), 'job_id': jid, 'owner': owner,
                   'employer_names': sorted(names), 'at': time.time()}
    if ui_only:
        reservation.update(duplicate_check=duplicate_check, duplicate_check_policy='crm_linkedin', crm_revision=revision)
    else:
        reservation['gmail_check'] = gmail_check
    reservations[jid] = reservation
    store.save(revision, state)
    _, verified = store.read()
    if verified.get('submission_reservations', {}).get(jid) != reservation:
        raise Held('Reservation write not verified')
    return reservation

def require_reservation(state, jid):
    from workflow.target_policy import require_target_policy
    require_target_policy(state, jid)
    from workflow.engine import require_application_route
    require_application_route(state, jid)
    if crm_linkedin_policy(state):
        if (state['paused'] and not paused_job_authorized(state, jid)) or state['submission_controls'].get('audit_status') != 'complete':
            raise Held('Audit incomplete or queue paused')
        reservation = state.get('submission_reservations', {}).get(jid, {})
        if reservation.get('job_id') != jid or reservation.get('duplicate_check_policy') != 'crm_linkedin':
            raise Held('Shared CRM/LinkedIn reservation required')
        require_linkedin_check(state, jid, reservation.get('duplicate_check'))
        return
    if state.get('submission_controls', {}).get('gmail_crosscheck_required'):
        if (state['paused'] and not paused_job_authorized(state, jid)) or state['submission_controls'].get('audit_status') != 'complete':
            raise Held('Audit incomplete or queue paused')
        sync = account_state(state, receipt_account())
        controls = state['submission_controls']
        per_job = controls.get('receipt_check_policy') == 'per_application' and controls.get('per_application_authorization')
        if not per_job and (not sync['backfill_complete'] or sync.get('query_version') != QUERY_VERSION):
            raise Held('Current Gmail backfill required')
        reservation = state.get('submission_reservations', {}).get(jid)
        check = (reservation or {}).get('gmail_check', {})
        if per_job and (check.get('scope') != 'all_time_employer_role_requisition'
                        or check.get('profile_verified') is not True
                        or check.get('source') not in ('gmail_connector', 'gmail_browser')):
            raise Held('Verified all-time per-application receipt check required')
        if (not reservation or reservation.get('job_id') != jid or check.get('job_id') != jid
            or check.get('account') != receipt_account() or not check.get('query')
            or check.get('all_pages_fetched') is not True or check.get('clear') is not True
            or not 0 <= time.time() - check.get('checked_at', 0) <= 600):
            raise Held('Fresh shared Gmail/CRM reservation required')

def install_audit_hold(store, report):
    """Persist the pause/preferences and cross-links without deleting source rows."""
    revision, state = store.read()
    state['paused'] = True
    state['submission_controls'] = {'audit_status': report['audit_status'],
        'gmail_crosscheck_required': True, 'gmail_plugin_first': True,
        'receipt_account': receipt_account(), 'audit_report': report.get('report_id', 'reviewed-application-audit'),
        'unresolved': report['limitations']}
    for group in report.get('duplicate_groups', []):
        state.setdefault('duplicate_groups', {})[group['id']] = copy.deepcopy(group)
        for jid in group['job_ids']:
            if jid not in state['jobs']: raise Held('Duplicate source row missing: ' + jid)
            job = state['jobs'][jid]
            job['duplicate_group'] = group['id']
            for other_id in group['job_ids']:
                if other_id != jid and other_id not in job.setdefault('possible_duplicates', []):
                    job['possible_duplicates'].append(other_id)
    store.save(revision, state)
    _, check = store.read()
    if (not check['paused'] or check.get('submission_controls') != state['submission_controls']
        or check.get('duplicate_groups') != state.get('duplicate_groups')
        or any(check['jobs'].get(jid) != state['jobs'][jid]
               for group in report.get('duplicate_groups', []) for jid in group['job_ids'])):
        raise Held('Audit hold write not verified')
    return {'audit_hold_verified': True}

def apply_audit(store, report_path):
    """Apply the reviewed bundles to fresh state and verify all evidence at once."""
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text())
    bundles = []
    for filename in report['pending_bundles']:
        path = (report_path.parent / filename).resolve()
        if path.parent != report_path.parent:
            raise Held('Audit bundles must be beside the reviewed report')
        bundles.append(json.loads(path.read_text()))
    # Every mutation still performs its own fresh read and CAS; no stale snapshot
    # is written, and a partial failure can be retried idempotently.
    install_audit_hold(store, report)
    results = [apply_bundle(store, bundle) for bundle in bundles]
    revision, state = store.read()
    checks = []
    for bundle, result in zip(bundles, results):
        for event, outcome in zip(bundle['events'], result['outcomes']):
            key = (event['account'] + ':' + event['message_id'] if event['source'] == 'gmail_connector'
                   else event['account'] + ':browser:' + event['evidence_key'])
            if state.get('receipt_events', {}).get(key) != {'event': event, 'outcome': outcome}:
                raise Held('Final audit evidence readback differs')
            if outcome['status'] == 'reconciled':
                job = state['jobs'][outcome['job_id']]
                if key not in job.get('reconciliation_events', []):
                    raise Held('Receipt not linked to application')
                if event['status'] == 'submitted' and not job.get('application', {}).get('submission_verified_by_email'):
                    raise Held('Submission evidence not persisted')
                checks.append({'company': event['company'], 'job_id': outcome['job_id'], 'state': job['state']})
    for correction in report['pending_status_corrections']:
        if state['jobs'][correction['job_id']]['state'] != correction['to']:
            raise Held('Final lifecycle correction not verified')
    for group in report.get('duplicate_groups', []):
        if state.get('duplicate_groups', {}).get(group['id']) != group:
            raise Held('Duplicate group not verified')
        if any(state['jobs'][jid].get('duplicate_group') != group['id'] for jid in group['job_ids']):
            raise Held('Duplicate source row not linked')
    if not state['paused'] or state.get('submission_controls', {}).get('audit_status') != report['audit_status']:
        raise Held('Final audit hold not verified')
    from collections import Counter
    return {'audit_corrections_verified': True, 'revision': revision, 'jobs': len(state['jobs']),
            'lifecycle_counts': dict(Counter(j['state'] for j in state['jobs'].values())),
            'receipt_events': len(state.get('receipt_events', {})), 'queue_paused': True,
            'checkpoint_advanced': any(r['checkpoint_advanced'] for r in results),
            'evidence_checks': checks}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['plan', 'reconcile', 'reserve', 'install-hold', 'apply-audit'])
    parser.add_argument('--remote')
    parser.add_argument('--credential-stdin', action='store_true',
                        help='Read the existing Sites owner credential once, hidden and only in memory')
    parser.add_argument('--db', default='workflow/runtime/app.sqlite')
    parser.add_argument('--input', required=True)
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text())
    if args.credential_stdin and not args.remote:
        parser.error('--credential-stdin requires --remote')
    token = None
    if args.credential_stdin:
        from workflow.credentials import read_owner_credential
        token = read_owner_credential()
    backend = RemoteStore(args.remote, token=token) if args.remote else LocalStore(args.db)
    if args.action == 'plan': result = plan(backend.read()[1], **data)
    elif args.action == 'reconcile': result = apply_bundle(backend, data)
    elif args.action == 'install-hold': result = install_audit_hold(backend, data)
    elif args.action == 'apply-audit': result = apply_audit(backend, args.input)
    else: result = reserve(backend, **data)
    print(json.dumps(result))

if __name__ == '__main__': main()
