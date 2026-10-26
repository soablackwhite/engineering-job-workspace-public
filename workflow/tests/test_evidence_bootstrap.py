import json
import tempfile
import unittest
from pathlib import Path
from workflow.evidence import digest
from workflow.evidence_bootstrap import build, private_json

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        text = 'skill.TensorFlow.years: 1\n\nLatest confirmed correction: one year.'
        (self.root/'facts.md').write_text(text)
        self.config = dict(vault=str(self.root), paths=['facts.md'], cache=str(self.root/'cache.sqlite'),
                           required_facts=['skill.TensorFlow.years','authorization.US'],
                           provenance=[dict(source='facts.md',source_sha256=digest(text),
                           quote='Latest confirmed correction: one year.',key='skill.TensorFlow.years',value=1)])
    def test_cold_warm_integrity_and_missing(self):
        first,a = build(self.config);second,b = build(self.config)
        self.assertEqual(first,second)
        self.assertEqual((a['cache_misses'],b['cache_hits']),(1,1))
        self.assertTrue(a['integrity'])
        self.assertEqual(a['missing_required'],['authorization.US'])
    def test_changed_source_cannot_reuse_approval(self):
        build(self.config)
        (self.root/'facts.md').write_text('skill.TensorFlow.years: 0')
        with self.assertRaises(ValueError):build(self.config)
    def test_unapproved_typed_value_rejected(self):
        self.config['provenance'][0]['value']=2
        with self.assertRaises(ValueError):build(self.config)
    def test_private_output_and_cache(self):
        index,a = build(self.config)
        target=self.root/'output.json';private_json(target,index)
        self.assertEqual(target.stat().st_mode & 0o777,0o600)
        self.assertEqual((self.root/'cache.sqlite').stat().st_mode & 0o777,0o600)
    def test_outside_vault_refused(self):
        self.config['paths']=['../outside.md']
        with self.assertRaises(ValueError):build(self.config)

    def test_changed_approved_pdf_holds(self):
        import hashlib
        pdf=self.root/'approved.pdf';pdf.write_bytes(b'approved-fixture')
        self.config['artifact_provenance']=[dict(path=str(pdf),sha256=hashlib.sha256(pdf.read_bytes()).hexdigest())]
        build(self.config);pdf.write_bytes(b'different-fixture')
        with self.assertRaises(ValueError):build(self.config)

    def test_minimal_merge_preserves_questions_history_and_existing_answers(self):
        from workflow.evidence_bootstrap import candidate_merge_proposal
        import hashlib
        pdf=self.root/'approved.pdf';pdf.write_bytes(b'approved')
        checksum=hashlib.sha256(pdf.read_bytes()).hexdigest()
        (self.root/'facts.md').write_text('professional_years: 2\n\nauthorization.US: false\n\ndocument.master.sha256: "'+checksum+'"')
        self.config['provenance']=[]
        index,_=build(self.config)
        candidate=dict(needs_input=[{'field':key} for key in ['engineering_experience','us_work_authorization','education_completion','valoria_delivery_status']],
                       application_answers={'old_scoped':{'value':'historical'}},base_documents=['old-doc'],master_document_id='old-doc',master_pdf_sha256='old-hash')
        prepared=dict(candidate_patch={'evidence_index':index,'application_answers':{'phone':{'value':'confirmed'}}},
                      approved_resume=dict(sha256=checksum,local_path=str(pdf),filename='approved.pdf'))
        result=candidate_merge_proposal(candidate,prepared);patch=result['candidate_patch']
        self.assertEqual(result['preserved_blockers'],['education_completion','valoria_delivery_status'])
        self.assertEqual(patch['base_documents'][0],'old-doc')
        self.assertIn('old_scoped',patch['application_answers'])
        self.assertFalse(result['submission_ready'])
        self.assertIsNone(result['documents_patch'][patch['master_document_id']]['review'])
        self.assertTrue(result['documents_patch'][patch['master_document_id']]['held'])
        self.assertEqual(candidate['master_document_id'],'old-doc')
