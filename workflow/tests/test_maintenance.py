"""Synthetic local fixtures; never reads private candidate data or connects out."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from workflow.evidence import digest, parse, signature
from workflow.maintenance import activate_approved_master


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        writer = PdfWriter(); writer.add_blank_page(width=612, height=792)
        stream = io.BytesIO(); writer.write(stream)
        raw = stream.getvalue(); pdf = self.root/'approved.pdf'; pdf.write_bytes(raw)
        self.resume = dict(filename='approved.pdf', sha256=hashlib.sha256(raw).hexdigest(),
                           local_path=str(pdf), pages=1, bytes=len(raw),
                           source_task='synthetic-user-approval', user_confirmed_on='2026-10-20')
        text = ('document.master.sha256: '+json.dumps(self.resume['sha256'])+
                '\n\ndocument.master.bytes: '+str(len(raw))+'\n')
        (self.root/'facts.md').write_text(text)
        manifest = {'facts.md': digest(text)}
        index = dict(vault=str(self.root), paths=['facts.md'], manifest=manifest,
                     fingerprint=signature(manifest), records=parse('facts.md', text))
        self.state = dict(paused=True, mode='dry_run', candidate=dict(evidence_index=index,
            master_document_id='old-doc', master_pdf_sha256='old-hash', base_documents=['old-doc'],
            needs_input=[dict(field='old-unknown', reason='Historical warning remains')],
            source_claim='Must not become a CV claim', application_drafting_context=dict(
                approved_submission_resume=dict(sha256='old-hash'), other='preserve')),
            documents={'old-doc': dict(id='old-doc', pdf_sha256='old-hash', held=True)},
            jobs={'submitted': dict(application=dict(resume_sha256='old-hash'), held=True)},
            cloud_execution=dict(submission_authorized=False, user_submission_hold=dict(active=True)),
            submission_controls=dict(final_review_required=True), submission_reservations={})

    def test_activation_is_pure_and_preserves_history_permissions_and_warnings(self):
        original = copy.deepcopy(self.state); approved = copy.deepcopy(self.resume)
        result = activate_approved_master(self.state, self.resume)
        self.assertEqual(self.state, original); self.assertEqual(self.resume, approved)
        updated, document_id = result['state'], result['document_id']
        self.assertEqual(updated['candidate']['master_pdf_sha256'], approved['sha256'])
        self.assertEqual(updated['candidate']['base_documents'], [document_id])
        self.assertEqual(updated['candidate']['cv_reference_history'][0]['previous']['master_document_id'], 'old-doc')
        self.assertEqual(updated['documents']['old-doc'], original['documents']['old-doc'])
        for key in ('jobs', 'cloud_execution', 'submission_controls', 'submission_reservations', 'mode', 'paused'):
            self.assertEqual(updated[key], original[key])
        self.assertEqual(updated['candidate']['needs_input'], original['candidate']['needs_input'])
        self.assertEqual(updated['candidate']['application_drafting_context']['other'], 'preserve')
        doc = updated['documents'][document_id]
        self.assertTrue(doc['held']); self.assertIsNone(doc['review']); self.assertEqual(doc['claims'], [])
        self.assertEqual(doc['hold_reasons'][0]['field'], 'independent_current_document_review')
        self.assertNotIn('source_claim', doc)
        self.assertFalse(result['proof']['permissions_changed'])

    def test_unpaused_input_is_rejected_without_mutation(self):
        self.state['paused'] = False; original = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, 'paused=True'):
            activate_approved_master(self.state, self.resume)
        self.assertEqual(self.state, original)

    def test_artifact_and_index_mismatches_fail_closed(self):
        for field, value in [('sha256', '0'*64), ('bytes', self.resume['bytes']+1), ('pages', 2)]:
            with self.subTest(field=field):
                resume = dict(self.resume, **{field: value})
                with self.assertRaises(ValueError):activate_approved_master(self.state, resume)
        # Supplied/indexed hashes match, but the actual saved artifact changed.
        path = Path(self.resume['local_path']); path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError, 'PDF bytes/hash mismatch'):
            activate_approved_master(self.state, self.resume)
        # A changed fact source cannot be blessed by stale cached records.
        (self.root/'facts.md').write_text('document.master.sha256: "different"\n')
        with self.assertRaisesRegex(ValueError, 'stale or unavailable'):
            activate_approved_master(self.state, self.resume)

    def test_repeated_activation_is_idempotent_and_document_conflicts_are_rejected(self):
        first = activate_approved_master(self.state, self.resume)
        # Operational publication/review metadata must survive reuse unchanged.
        doc = first['state']['documents'][first['document_id']]
        doc.update(pdf_available=True, review=dict(passed=True, reviewer='synthetic-independent-reviewer'))
        second = activate_approved_master(first['state'], self.resume)
        self.assertEqual(second['document_id'], first['document_id'])
        self.assertEqual(second['state'], first['state'])
        self.assertFalse(second['proof']['document_created'])
        self.assertFalse(second['proof']['active_references_changed'])
        forged = copy.deepcopy(first['state'])
        forged['documents'][first['document_id']]['pdf_sha256'] = 'forged'
        with self.assertRaisesRegex(ValueError, 'Immutable approved-master document conflict'):
            activate_approved_master(forged, self.resume)

    def test_optional_source_is_verified_and_metadata_is_not_inferred(self):
        source = self.root/'approved.tex'; source.write_text('synthetic source')
        resume = dict(self.resume, canonical_source_path=str(source),
                      source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        result = activate_approved_master(self.state, resume)
        self.assertEqual(result['proof']['source_sha256'], resume['source_sha256'])
        with self.assertRaisesRegex(ValueError, 'Paired source hash mismatch'):
            activate_approved_master(self.state, dict(resume, source_sha256='0'*64))
        with self.assertRaisesRegex(ValueError, 'supplied together'):
            activate_approved_master(self.state, dict(self.resume, canonical_source_path=str(source)))
        with self.assertRaisesRegex(ValueError, 'metadata is incomplete'):
            activate_approved_master(self.state, {'sha256': self.resume['sha256']})
