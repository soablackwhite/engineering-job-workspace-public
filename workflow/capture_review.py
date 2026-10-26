"""Coordinator-recorded independent capture review, outside extraction JSON.

The configured reviewer independently extracts from the description, without the
first extractor's answer. Agreement is necessary, not proof of model correctness.
Production must provide a trusted separate reviewer adapter; there is no default.
"""
import copy
import re


def review_body(extracted):
    if not isinstance(extracted, dict): return None
    observations = extracted.get('observations')
    requirements = extracted.get('requirements')
    if not isinstance(observations, dict) or not isinstance(requirements, list): return None
    # Timestamps and worker-local IDs may differ; scope, quotes and values may not.
    body = copy.deepcopy({'observations': observations, 'requirements': requirements})
    for row in body['observations'].values():
        if not isinstance(row, dict): return None
        row.pop('checked_at', None)
    for row in body['requirements']:
        if not isinstance(row, dict): return None
        row.pop('id', None)
    import json
    body['requirements'].sort(key=lambda row: json.dumps(row, sort_keys=True))
    return body


def semantic_issues(extracted):
    """Catch explicit numeric contradictions; other semantics require review.

    This is deliberately not a universal requirement parser. Ambiguous or missing
    numeric support holds instead of silently treating the minimum as zero.
    """
    if not isinstance(extracted, dict): return []
    observations=extracted.get('observations', {})
    if not isinstance(observations,dict):return []
    row = observations.get('minimum_years', {})
    if not isinstance(row, dict): return []
    value = row.get('value'); quote = row.get('quote', '')
    if value is None: return []
    if not isinstance(quote, str): return ['minimum_years:unsupported_value']
    words = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
             'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
    normalized = quote.casefold()
    for word, number in words.items(): normalized = re.sub(r'\b' + word + r'\b', str(number), normalized)
    matches = re.findall(r'\b(\d+(?:\.\d+)?)\s*(?:[–-]\s*\d+(?:\.\d+)?)?\s*\+?\s*years?\b', normalized)
    if matches and type(value) in (int, float) and value in [float(v) for v in matches]: return []
    if value == 0 and re.search(r'no (?:prior |previous )?experience (?:is )?required|open to .{0,15}new grads', normalized): return []
    return ['minimum_years:unsupported_value']


def agreement_body(extracted):
    """Compare interpreted facts, allowing different valid source quote spans."""
    body=review_body(extracted)
    if body is None:return None
    for row in body['observations'].values():row.pop('quote',None)
    for row in body['requirements']:row.pop('text',None)
    import json
    body['requirements'].sort(key=lambda row:json.dumps(row,sort_keys=True))
    return body


def source_issues(job, extracted):
    body=review_body(extracted)
    if body is None:return ['malformed_capture']
    snapshot=job['snapshots'][-1]
    issues=[]
    for key,row in body['observations'].items():
        quote=row.get('quote')
        if not isinstance(quote,str) or not quote.strip() or quote not in snapshot['text'] or row.get('source_url')!=snapshot['source_url'] or row.get('scope')!='role':
            issues.append(key+':unsupported_source')
    for row in body['requirements']:
        quote=row.get('text')
        if not isinstance(quote,str) or not quote.strip() or quote not in snapshot['text']:issues.append('requirement:unsupported_source')
    return issues
