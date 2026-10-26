"""Paused-state reference maintenance; no persistence or application actions.

The caller owns storage/CAS, document publication and readback. This helper reads
the explicitly supplied local artifacts and evidence sources, then returns a
deep-copied state. It never writes files, changes a historical document/job,
clears a hold, or grants submission permission.
"""
from __future__ import annotations

import copy
import hashlib
import io
import re
from datetime import date
from pathlib import Path

from workflow.engine import sha
from workflow.evidence import current, fact_value


_REQUIRED = {'filename', 'sha256', 'local_path', 'pages', 'bytes',
             'source_task', 'user_confirmed_on'}
_OPTIONAL = {'canonical_source_path', 'source_sha256'}
_FACTS = ['document.master.sha256', 'document.master.bytes']


def _valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value)


def activate_approved_master(state, resume):
    """Return ``{state, document_id, proof}`` without mutating either input.

    ``resume`` contains the approved artifact's filename, sha256, local_path,
    pages, bytes, source_task and ISO user_confirmed_on date. Optional paired
    source path/hash must be supplied together. Artifact/source hashes verify
    saved bytes; they do not assert a reproducible build or semantic approval.

    A new document has empty generated claims and remains held for independent
    current review. Repeated activation of an identical artifact is idempotent;
    existing operational review/publication metadata is preserved, not reset.
    """
    if not isinstance(state, dict) or state.get('paused') is not True:
        raise ValueError('Approved-master maintenance requires paused=True')
    if not isinstance(resume, dict) or not _REQUIRED <= resume.keys():
        raise ValueError('Approved resume metadata is incomplete')
    if resume.keys() - (_REQUIRED | _OPTIONAL):
        raise ValueError('Unexpected approved resume metadata field')
    for key in ('filename', 'local_path', 'source_task', 'user_confirmed_on'):
        if not isinstance(resume[key], str) or not resume[key].strip():
            raise ValueError('Approved resume metadata needs '+key)
    if not _valid_hash(resume['sha256']):
        raise ValueError('Approved resume SHA256 is invalid')
    for key in ('pages', 'bytes'):
        if type(resume[key]) is not int or resume[key] <= 0:
            raise ValueError('Approved resume '+key+' must be a positive integer')
    try:
        date.fromisoformat(resume['user_confirmed_on'])
    except ValueError as error:
        raise ValueError('Approved resume confirmation date must be ISO format') from error
    if ('canonical_source_path' in resume) != ('source_sha256' in resume):
        raise ValueError('Paired source path and SHA256 must be supplied together')

    candidate = state.get('candidate')
    documents = state.get('documents')
    if not isinstance(candidate, dict) or not isinstance(documents, dict):
        raise ValueError('Candidate and document registry must be objects')
    context = candidate.get('application_drafting_context', {})
    history = candidate.get('cv_reference_history', [])
    if not isinstance(context, dict) or not isinstance(history, list):
        raise ValueError('Malformed candidate reference context/history')
    index = candidate.get('evidence_index', {})
    if not current(index, _FACTS):
        raise ValueError('Current approved-master evidence is stale or unavailable')
    if fact_value(index, _FACTS[0]) != resume['sha256']:
        raise ValueError('Approved resume hash differs from current indexed fact')
    if fact_value(index, _FACTS[1]) != resume['bytes']:
        raise ValueError('Approved resume bytes differ from current indexed fact')

    raw = Path(resume['local_path']).read_bytes()
    if len(raw) != resume['bytes'] or hashlib.sha256(raw).hexdigest() != resume['sha256']:
        raise ValueError('Approved PDF bytes/hash mismatch')
    from pypdf import PdfReader
    if not raw.startswith(b'%PDF-'):
        raise ValueError('Approved artifact is not a PDF')
    reader = PdfReader(io.BytesIO(raw))
    if reader.is_encrypted or len(reader.pages) != resume['pages']:
        raise ValueError('Approved PDF encryption/page-count mismatch')
    if 'canonical_source_path' in resume:
        path, checksum = resume['canonical_source_path'], resume['source_sha256']
        if not isinstance(path, str) or not path.strip() or not _valid_hash(checksum):
            raise ValueError('Paired source metadata is invalid')
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != checksum:
            raise ValueError('Paired source hash mismatch')

    # Artifact identity/provenance is immutable. Review/publication fields are
    # operational metadata outside the identity core and are never overwritten.
    core = dict(variant='master', writer='approved-master-maintenance',
                filename=resume['filename'], pdf_path=resume['local_path'],
                pdf_sha256=resume['sha256'], bytes=resume['bytes'],
                pages=resume['pages'], dynamic_tailoring=False, claims=[],
                source_approval=copy.deepcopy(resume))
    if 'source_sha256' in resume:
        core['source_sha256'] = resume['source_sha256']
        core['canonical_source_path'] = resume['canonical_source_path']
    document_id = sha(core)
    existing = documents.get(document_id)
    if existing is not None and (not isinstance(existing, dict) or
            any(existing.get(key) != value for key, value in core.items()) or
            existing.get('id') != document_id):
        raise ValueError('Immutable approved-master document conflict')

    previous = {key: copy.deepcopy(candidate.get(key))
                for key in ('master_document_id', 'master_pdf_sha256', 'base_documents')}
    previous['approved_submission_resume'] = copy.deepcopy(context.get('approved_submission_resume'))
    active = dict(master_document_id=document_id, master_pdf_sha256=resume['sha256'],
                  base_documents=[document_id], approved_submission_resume=copy.deepcopy(resume))
    changed = previous != active
    updated = copy.deepcopy(state)
    if existing is None:
        updated['documents'][document_id] = dict(core, id=document_id, held=True,
            hold_reasons=[dict(field='independent_current_document_review',
                               reason='Current fixed artifact requires independent review; reference activation is not submission approval.')],
            review=None)
    if changed:
        record = dict(event='approved_master_reference_activation', previous=previous,
                      replacement_document_id=document_id)
        record['id'] = sha(record)
        updated['candidate'].setdefault('cv_reference_history', []).append(record)
        updated['candidate'].update({key: active[key] for key in
                                     ('master_document_id', 'master_pdf_sha256', 'base_documents')})
        updated['candidate'].setdefault('application_drafting_context', {})['approved_submission_resume'] = copy.deepcopy(resume)
    return dict(state=updated, document_id=document_id,
                proof=dict(pdf_sha256=resume['sha256'], pdf_bytes=len(raw),
                           page_count=len(reader.pages), index_fingerprint=index.get('fingerprint'),
                           source_sha256=resume.get('source_sha256'), document_created=existing is None,
                           active_references_changed=changed, paused=True,
                           permissions_changed=False, historical_jobs_changed=False))
