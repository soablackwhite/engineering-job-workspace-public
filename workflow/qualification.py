"""Fail-closed eligibility and evidence matrix; no keyword-fit score."""
from __future__ import annotations
from datetime import datetime, timezone
import math
import re
from workflow.evidence import fact_value, facts as indexed_facts

VERSION = 'evidence-gated-2'
SPONSORSHIP = {'explicit_yes', 'credible_history', 'unknown', 'explicit_no',
               'work_authorization_required_no_sponsorship'}
FAMILIES = {'applied_ai', 'product_fde'}


def role_family(title):
    text = title.casefold()
    if re.search(r'\b(product (?:marketing )?manager|associate product manager|apm)\b', text): return 'outside_default'
    if re.search(r'\b(product engineer|forward.deployed)\b', text): return 'product_fde'
    if re.search(r'\b(applied ai|ai platform|ai engineer|artificial intelligence)\b', text): return 'applied_ai'
    return 'unknown'


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def fresh(date, now, max_days=30):
    try:
        parsed = datetime.fromisoformat(date.replace('Z', '+00:00'))
        if parsed.tzinfo is None: return False
        age = (now - parsed).total_seconds()
        return 0 <= age <= max_days * 86400
    except (TypeError, ValueError, AttributeError): return False


def screen(job, candidate, extracted, now=None, selection_policy=None):
    """Extraction supplies observations, never a decision or inferred evidence.

    An independent capture review must bind the complete requirement inventory
    and value/quote mappings. Without it even well-formed extraction stays held.
    """
    from workflow.engine import sha
    now = now or datetime.now(timezone.utc)
    from workflow.target_policy import installed, FAMILIES as TARGET_FAMILIES, title_family
    targeted = installed(selection_policy)
    failures, questions, matrix = [], [], []
    index = candidate.get('evidence_index', {})
    if not index: questions.append('obsidian_evidence_index')
    snapshot = job['snapshots'][-1] if job.get('snapshots') else {}
    text = snapshot.get('text', '')
    if not text: questions.append('full_description')
    if not isinstance(extracted, dict):
        return dict(decision='needs_input', failures=[], questions=['malformed_extraction'], requirement_matrix=[])
    observations = extracted.get('observations', {})
    if not isinstance(observations, dict): observations = {}; questions.append('malformed_observations')
    def value(key):
        row = observations.get(key)
        if (not isinstance(row, dict) or not isinstance(row.get('quote'), str) or not row['quote'].strip()
            or row['quote'] not in text or row.get('source_url') != snapshot.get('source_url')
            or row.get('scope') != 'role' or not fresh(row.get('checked_at'), now)
            or row.get('conditional') or row.get('conflicting')):
            questions.append(key + ':unverified_stale_or_conflicting'); return None
        return row.get('value')
    requirements = extracted.get('requirements')
    if not isinstance(requirements, list): requirements = []; questions.append('requirement_inventory')
    # Model-authored capture_review is ignored. Only the coordinator's separate
    # reviewer invocation can create an attestation on the job record.
    from workflow.capture_review import review_body, semantic_issues
    review = job.get('capture_attestation', {})
    body = review_body(extracted)
    if (body is None or review.get('snapshot_hash') != snapshot.get('hash')
        or review.get('body_hash') != sha(body) or review.get('passed') is not True
        or review.get('policy_version') != VERSION):
        questions.append('independent_requirement_capture_review')
    questions.extend(semantic_issues(extracted))
    family = value('role_family')
    if not isinstance(family,str) or family not in (TARGET_FAMILIES if targeted else FAMILIES):
        (failures if family == 'outside_default' else questions).append('role_family')
    if role_family(job.get('title', '')) == 'outside_default': failures.append('role_family')
    if targeted and title_family(job.get('title', '')) != family: failures.append('role_family')
    country = value('country')
    if not isinstance(country, str) or not re.fullmatch('[A-Z]{2}', country):
        questions.append('country'); country = 'unknown'
    auth = fact_value(index, 'authorization.' + country)
    needs = fact_value(index, 'sponsorship_needed.' + country)
    if type(auth) is not bool or type(needs) is not bool: questions.append('country_authorization_and_sponsorship')
    sponsor = value('sponsorship')
    if not isinstance(sponsor,str) or sponsor not in SPONSORSHIP: questions.append('sponsorship_classification')
    # Unknown/conditional/stale/conflicting role evidence always requires review.
    if sponsor in ('unknown', 'credible_history'): questions.append('role_sponsorship_confirmation')
    if needs is True and sponsor in ('explicit_no', 'work_authorization_required_no_sponsorship'):
        failures.append('sponsorship_unavailable')
    if auth is False and needs is False: questions.append('authorization_path')
    seniority = value('seniority')
    if targeted:
        if seniority in ('entry', 'junior', 'graduate', 'intern'): failures.append('seniority')
        elif seniority not in ('mid', 'senior', 'staff', 'principal', 'lead', 'not_junior'): questions.append('seniority')
    else:
        if seniority in ('senior', 'staff', 'principal', 'lead'): failures.append('seniority')
        elif seniority not in ('entry', 'junior', 'mid'): questions.append('seniority')
    minimum = value('minimum_years'); years = fact_value(index, 'professional_years')
    if not number(minimum) or not number(years): questions.append('professional_years')
    elif minimum > years: failures.append('minimum_years')
    location = value('location')
    residence = fact_value(index, 'residence_country')
    if not isinstance(location, dict) or location.get('mode') not in ('remote', 'onsite', 'hybrid'):
        questions.append('location')
    elif location['mode'] == 'remote':
        permitted = location.get('allowed_countries')
        if not isinstance(permitted, list) or not permitted or not isinstance(residence, str): questions.append('remote_location')
        elif residence not in permitted:
            # A US-remote employment location is compatible with moving there.
            # The separate target preflight still requires positive overseas
            # hiring, new sponsorship and relocation-assistance evidence.
            if targeted and country in permitted and fact_value(index, 'relocate.'+country) is True:
                pass
            else: failures.append('remote_location')
    else:
        willing = fact_value(index, 'relocate.' + country)
        if residence != country:
            if willing is False: failures.append('relocation')
            elif willing is not True: questions.append('relocation')
    pay = value('compensation'); floor = fact_value(index, 'minimum_compensation')
    if not isinstance(floor, dict) or not number(floor.get('amount')): questions.append('candidate_compensation')
    elif (not isinstance(pay, dict) or not number(pay.get('maximum'))
          or pay.get('currency') != floor.get('currency') or pay.get('period') != floor.get('period')):
        questions.append('compensation_unknown_or_incomparable')
    elif pay['maximum'] < floor['amount']: failures.append('compensation_below_minimum')
    known = indexed_facts(index)
    ids = set()
    for row in requirements:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in ids
            or type(row.get('mandatory')) is not bool or not isinstance(row.get('text'), str)
            or not row['text'] or row['text'] not in text or not isinstance(row.get('fact_key'), str)
            or row.get('operator') not in ('equals', 'at_least') or 'value' not in row):
            questions.append('malformed_requirement'); continue
        ids.add(row['id']); evidence = known.get(row['fact_key'], {})
        actual = None if evidence.get('conflict') else evidence.get('value')
        if actual is None: strength, gap = 'unknown', 'missing_or_conflicting_evidence'
        elif row['operator'] == 'at_least':
            if not number(actual) or not number(row['value']): strength, gap = 'unknown', 'invalid_numeric_scope'
            elif actual >= row['value']: strength, gap = 'direct', None
            else: strength, gap = 'unmet', 'below_required_value'
        elif type(actual) is type(row['value']) and actual == row['value']: strength, gap = 'direct', None
        else: strength, gap = 'unmet', 'different_value'
        blocker = row['mandatory'] and strength != 'direct'
        if blocker: (failures if strength == 'unmet' else questions).append('requirement:' + row['id'])
        matrix.append(dict(requirement=row, evidence_ids=evidence.get('evidence_ids', []),
                           actual=actual, match_strength=strength, gap=gap, blocker=blocker))
    return dict(policy_version=VERSION, decision='excluded' if failures else 'needs_input' if questions else 'eligible',
                failures=sorted(set(failures)), questions=sorted(set(questions)), requirement_matrix=matrix,
                role_family=family or 'unknown', country=country, sponsorship=sponsor or 'unknown',
                seniority=seniority or 'unknown', observations=observations,
                evaluated_at=now.isoformat())


def triage(job, candidate):
    """Cheap exclusions only. This can never grant eligibility or submission.

    Restrict tenure matches to explicit software/product engineering requirements.
    Alternatives, preferred sections and ambiguous prose go to full review.
    """
    from workflow.evidence import current
    title=job.get('title','')
    if role_family(title)=='outside_default':return {'reason':'role_family','quote':title}
    if re.search(r'\b(senior|staff|principal|lead)\b',title,re.I):return {'reason':'seniority','quote':title}
    index=candidate.get('evidence_index',{})
    if not current(index,['professional_years']):return None
    actual=fact_value(index,'professional_years')
    if not number(actual):return None
    optional=False
    for line in (job.get('snapshots') or [{}])[-1].get('text','').splitlines():
        plain=re.sub(r'^L\d+:\s*','',line).strip()
        if re.fullmatch(r'(bonus|preferred|nice.to.have).*?(qualifications|experience|skills)?[: ]*',plain,re.I):optional=True
        if re.fullmatch(r'(requirements|minimum qualifications|what you.ll bring|candidate profile)[: ]*',plain,re.I):optional=False
        if re.search(r'\d+\s*[–-]\s*\d+',plain):continue
        if optional or re.search(r'\b(prefer\w*|bonus|typically|approximately|or|equivalent|up to)\b',plain,re.I):continue
        match=re.search(r'\b(\d+)\s*\+?\s*years\s+(?:of\s+|in\s+)?(?:strong\s+)?(?:professional\s+)?(?:software|product)\s+engineering\s+(?:experience|environments)\b',plain,re.I)
        if match and int(match[1])>actual:
            return {'reason':'minimum_years','quote':line,'minimum':int(match[1]),'actual':actual,'fact_key':'professional_years'}
    return None
