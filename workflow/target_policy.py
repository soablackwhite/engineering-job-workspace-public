"""Shared, opt-in targeting preflight. It never grants submission permission.

Missing policy preserves legacy behavior. Once installed, missing/changed proof
holds preparation; historical receipt/status reconciliation does not call this.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import math
import re

VERSION = '2026-10-26-fde-abroad-v1'
FAMILIES = {'forward_deployed_engineer', 'solutions_engineer'}
SOURCE_KINDS = {'employer_role', 'recruiter_role', 'employer_policy', 'primary_exchange_rate'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def policy_digest(policy):
    return digest({k: v for k, v in policy.items() if k != 'digest'})


def installed(policy):
    return isinstance(policy, dict) and policy.get('version') == VERSION and policy.get('enabled') is True


def title_family(title):
    if re.search(r'\bforward[ -]+deploy(?:ed|ment)\b[^|;]*\bengineer\b|\bfde\b', title, re.I):
        return 'forward_deployed_engineer'
    if re.search(r'\bsolutions? engineer\b', title, re.I):
        return 'solutions_engineer'
    return 'outside_scope'


def _fresh(at, now, days):
    try:
        parsed = datetime.fromisoformat(at.replace('Z', '+00:00'))
        return parsed.tzinfo is not None and 0 <= (now-parsed).total_seconds() <= days*86400
    except (AttributeError, TypeError, ValueError):
        return False


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def require_target_policy(state, jid, now=None):
    """Require reviewed exact-role proof, in addition to all existing gates.

    The reviewer is responsible for semantics of literal source quotations.
    This checks scope, provenance, freshness and bindings rather than pretending
    keywords alone can establish overseas hiring or technical qualification.
    """
    from workflow.engine import Held, sha
    if 'application_selection_policy' not in state:
        return None
    policy = state['application_selection_policy']
    def hold(reason):
        raise Held('Target policy: ' + reason)
    if not installed(policy):
        hold('installed policy is disabled, malformed or unsupported')
    if state.get('paused') is not False:
        hold('queue paused; target review cannot authorize execution')
    required = dict(exclude_junior=True, require_new_visa_sponsorship=True,
                    require_abroad_applicants=True, require_relocation_assistance=True,
                    gb_location='London', minimum_annual_usd=110000, compensation_basis='base')
    if any(type(policy.get(k)) is not type(v) or policy[k] != v for k, v in required.items()):
        hold('required policy constraints are missing or changed')
    if (policy.get('allowed_role_families') != sorted(FAMILIES)
        or policy.get('allowed_countries') != ['US', 'GB']
        or type(policy.get('max_evidence_age_days')) is not int
        or not 0 < policy['max_evidence_age_days'] <= 30
        or not isinstance(policy.get('current_cv_sha256'), str)
        or not re.fullmatch('[0-9a-f]{64}', policy.get('current_cv_sha256', ''))):
        hold('invalid scope, freshness or approved CV configuration')
    bound_policy = policy_digest(policy)
    if policy.get('digest') != bound_policy:
        hold('policy digest is stale')
    now = now or datetime.now(timezone.utc)
    job = state.get('jobs', {}).get(jid)
    if not isinstance(job, dict): hold('exact job is missing')
    snapshots = job.get('snapshots') or []
    if (not isinstance(snapshots, list) or not snapshots or not isinstance(snapshots[-1], dict)
        or not snapshots[-1].get('text') or not snapshots[-1].get('hash')):
        hold('current full role capture required')
    snapshot = snapshots[-1]
    if not isinstance(snapshot, dict): hold('malformed role capture')
    if snapshot['hash'] != sha(snapshot['text']):
        hold('role capture content/hash mismatch')
    review = job.get('target_policy_review') or {}
    if not isinstance(review, dict): hold('malformed target review')
    if (review.get('job_id') != jid or review.get('decision') != 'eligible' or not review.get('reviewer')
        or review.get('policy_hash') != bound_policy
        or review.get('snapshot_hash') != snapshot['hash']
        or review.get('cv_sha256') != policy['current_cv_sha256']
        or not _fresh(review.get('checked_at'), now, policy['max_evidence_age_days'])):
        hold('missing or stale policy/snapshot/CV review')
    qualification = review.get('qualification_review') or {}
    if not isinstance(qualification, dict): hold('malformed qualification review')
    knowledge_hash = state.get('cloud_execution', {}).get('knowledge_snapshot', {}).get('sha256')
    if (qualification.get('passed') is not True or not qualification.get('reviewer')
        or qualification.get('snapshot_hash') != snapshot['hash']
        or not knowledge_hash or qualification.get('candidate_snapshot_sha256') != knowledge_hash):
        hold('current independent mandatory-qualification review required')
    sources = {'role_snapshot': dict(text=snapshot['text'], url=snapshot.get('source_url'),
                                    hash=snapshot['hash'], kind='employer_role', scope='role',
                                    checked_at=snapshot.get('checked_at'), job_ids=[jid])}
    extra_sources = job.get('target_policy_sources', [])
    if not isinstance(extra_sources, list): hold('malformed supplementary sources')
    for source in extra_sources:
        if (not isinstance(source, dict) or not isinstance(source.get('id'), str)
            or not source['id'] or source['id'] in sources):
            hold('malformed or duplicate supplementary source')
        sources[source.get('id')] = source
    observations = review.get('observations') or {}
    if not isinstance(observations, dict) or not isinstance(review.get('source_hashes'), dict):
        hold('malformed observations or source bindings')
    def observe(key, role_only=False):
        row = observations.get(key)
        if not isinstance(row, dict) or row.get('conditional') or row.get('conflicting'):
            hold(key + ' missing, conditional or conflicting')
        proofs = row.get('evidence')
        if not isinstance(proofs, list) or not proofs:
            hold(key + ' requires literal saved evidence')
        for proof in proofs:
            if not isinstance(proof, dict): hold(key + ' malformed evidence')
            sid = proof.get('source_id')
            if not isinstance(sid, str): hold(key + ' malformed source identity')
            source = sources.get(sid, {})
            text = source.get('text'); quote = proof.get('quote')
            if (not isinstance(text, str) or not isinstance(quote, str) or not quote.strip()
                or quote not in text or not isinstance(source.get('url'), str)
                or not source['url'].startswith('https://')
                or source.get('kind') not in SOURCE_KINDS
                or (role_only and source.get('scope') != 'role')
                or (not role_only and source.get('scope') not in ('role', 'company', 'fx'))
                or (source.get('scope') != 'fx' and jid not in source.get('job_ids', []))
                or not _fresh(source.get('checked_at'), now, policy['max_evidence_age_days'])
                or review.get('source_hashes', {}).get(sid) != source.get('hash')):
                hold(key + ' source is unbound, stale or outside exact role scope')
            if sid != 'role_snapshot' and source.get('hash') != hashlib.sha256(text.encode('utf-8')).hexdigest():
                hold(key + ' supplementary capture changed')
            if key != 'fx' and source.get('kind') == 'primary_exchange_rate':
                hold(key + ' needs employer/recruiter evidence')
            if key == 'fx' and source.get('kind') != 'primary_exchange_rate':
                hold('FX requires a primary exchange-rate capture')
            expected_scope = ('fx' if source['kind']=='primary_exchange_rate' else
                              'company' if source['kind']=='employer_policy' else 'role')
            if source.get('scope') != expected_scope: hold(key + ' source scope/kind mismatch')
        return row.get('value')
    family = observe('role_family', role_only=True)
    if family not in FAMILIES or title_family(job.get('title', '')) != family:
        hold('role must be an exact FDE or Solutions Engineer role')
    junior = re.search(r'\b(junior|jr\.?|graduate|intern(?:ship)?|entry[ -]level|new grad(?:uate)?s?)\b', job.get('title', ''), re.I)
    if junior or observe('seniority', role_only=True) not in ('mid', 'senior', 'staff', 'principal', 'lead', 'not_junior'):
        hold('junior/entry/graduate/intern or unverified level')
    country = observe('country', role_only=True)
    if country not in policy['allowed_countries']:
        hold('country outside US/London scope')
    if country == 'GB' and observe('city', role_only=True) != 'London':
        hold('UK role must be London')
    if observe('sponsorship') != 'new_visa_available':
        hold('explicit new employer visa sponsorship required; transfer-only/no-sponsor is excluded')
    if observe('abroad_applicants') != 'accepted':
        hold('explicit applicants-from-abroad support required')
    if observe('relocation_assistance') != 'available':
        hold('employer relocation assistance required')
    if observe('existing_authorization_required') is not False:
        hold('current-country work authorization must not be a prerequisite')
    pay = observe('compensation', role_only=True)
    if (not isinstance(pay, dict) or pay.get('period') != 'annual'
        or pay.get('currency') not in ('USD', 'GBP')
        or not _number(pay.get('minimum')) or not _number(pay.get('maximum'))
        or pay['minimum'] > pay['maximum'] or pay.get('basis') != 'base'):
        hold('explicit guaranteed annual base-pay range required; OTE/bonus/equity is insufficient')
    rate = 1
    if pay['currency'] == 'GBP':
        rate = observe('fx')
        if not _number(rate) or rate <= 0: hold('invalid GBP to USD rate')
    if pay['maximum']*rate < policy['minimum_annual_usd']:
        hold('annual pay range does not reach USD110000')
    return dict(policy_hash=bound_policy, snapshot_hash=snapshot['hash'],
                cv_sha256=policy['current_cv_sha256'], role_family=family, country=country,
                compensation_basis=pay['basis'], annual_usd_maximum=pay['maximum']*rate)
