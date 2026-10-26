"""Explicit fact dependencies; no cached private reasoning or whole-vault prompts."""
from workflow.evidence import dependency_snapshot


def screen_keys(extracted):
    rows = extracted.get('observations', {}) if isinstance(extracted, dict) else {}
    country_row=rows.get('country') if isinstance(rows,dict) else None
    country=country_row.get('value') if isinstance(country_row,dict) else None
    country = country if isinstance(country, str) else 'unknown'
    keys = {'professional_years', 'residence_country', 'minimum_compensation',
            'authorization.' + country, 'sponsorship_needed.' + country, 'relocate.' + country}
    reqs = extracted.get('requirements', []) if isinstance(extracted, dict) else []
    if isinstance(reqs, list):
        keys.update(r['fact_key'] for r in reqs if isinstance(r, dict) and isinstance(r.get('fact_key'), str))
    return sorted(keys)


def document_dependencies(doc):
    claims = doc.get('claims', [])
    if not isinstance(claims,list) or any(not isinstance(c,dict) for c in claims):
        from workflow.engine import Held
        raise Held('Malformed document claims')
    return (sorted({'document.master.sha256'} | {c['fact_key'] for c in claims if c.get('fact_key')}),
            sorted({i for c in claims for i in c.get('evidence_ids', [])}))


def candidate_binding(candidate, keys, ids=(), *, answers=()):
    from workflow.engine import sha
    return sha({'evidence': dependency_snapshot(candidate.get('evidence_index', {}), keys, ids),
                'policy': {k: candidate.get(k) for k in ('cv_policy', 'master_pdf_sha256')},
                'answers': {k: candidate.get('application_answers', {}).get(k) for k in sorted(answers)}})

# No built-in candidate-specific scopes. Missing categories remain global holds.
# Reviewed private candidate data may supply {field: {subject, concepts}}.


def unresolved_for_package(candidate, doc, answers, extracted=None, field_bindings=None):
    """Keep unresolved facts intact; select holds needed by this exact package.

    Claims and explicit document fact dependencies name their sources. A fixed
    approved PDF still needs independent semantic review and retains its held
    flag: absence of generated claims does not approve that document.
    """
    from workflow.engine import Held
    keys, _ = document_dependencies(doc)
    signals = list(keys) + screen_keys(extracted)
    for claim in doc.get('claims', []):
        signals.extend(str(claim.get(k, '')) for k in ('fact_key', 'text'))
    declared = doc.get('fact_dependencies', [])
    if not isinstance(declared, list) or any(not isinstance(k, str) for k in declared):
        raise Held('Malformed explicit document dependencies')
    signals.extend(declared)
    if field_bindings is not None:
        if not isinstance(field_bindings, dict): raise Held('Malformed question bindings')
        signals.extend(str(field_bindings.get(key, {}).get('label', '')) for key in answers)
    records = candidate.get('application_answers', {})
    for key, value in answers.items():
        signals.extend((key, 'answer.' + key, str(value)))
        item = records.get(key, {})
        dependencies = item.get('fact_dependencies', [])
        if not isinstance(dependencies, list) or any(not isinstance(k, str) for k in dependencies):
            raise Held('Malformed explicit answer dependencies')
        signals.extend(dependencies)
    folded = [s.casefold().replace('-', '_').replace(' ', '_') for s in signals]
    unresolved = candidate.get('needs_input', [])
    if not isinstance(unresolved, list) or any(not isinstance(row, dict) for row in unresolved):
        raise Held('Malformed unresolved candidate facts')
    scopes = candidate.get('unresolved_scopes', {})
    if not isinstance(scopes, dict): raise Held('Malformed unresolved dependency scopes')
    for field, scope in scopes.items():
        if (not isinstance(field,str) or not isinstance(scope,dict)
            or not isinstance(scope.get('subject'),str) or not scope['subject']
            or not isinstance(scope.get('concepts'),list) or not scope['concepts']
            or any(not isinstance(c,str) or not c for c in scope['concepts'])):
            raise Held('Malformed unresolved dependency scope')
    def needed(field):
        if field not in scopes:
            return True
        subject, concepts = scopes[field]['subject'].casefold(), [c.casefold() for c in scopes[field]['concepts']]
        return any(field in signal or (subject in signal and any(word in signal for word in concepts))
                   for signal in folded)
    return [row for row in unresolved if needed(row.get('field'))]


def require_resolved_package(candidate, doc, answers, extracted=None, field_bindings=None):
    from workflow.engine import Held
    rows = unresolved_for_package(candidate, doc, answers, extracted, field_bindings)
    if rows:
        raise Held('Unresolved package facts: ' + ', '.join(str(row.get('field', 'unknown')) for row in rows))
