"""Generate temporary synthetic documents; never load a person's CV or private export."""
import copy,hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from workflow.resumes import verified_manifest,render,write,review

def synthetic_pdf():
    # Minimal standard PDF, built locally without TeX, network or personal data.
    stream=b'BT /F1 12 Tf 72 720 Td (Example Candidate) Tj 0 -20 Td (Experience) Tj 0 -20 Td (Example Engineering) Tj 0 -20 Td (Education) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    raw=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(raw));raw+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(raw);raw+=b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets[1:]:raw+=f'{offset:010d} 00000 n \n'.encode()
    return raw+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'

class MasterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.pdf=self.root/'approved.pdf';self.pdf.write_bytes(synthetic_pdf())
        self.source=self.root/'approved.txt';self.source.write_text('Synthetic source for Example Candidate')
        self.manifest=dict(candidate_commit='synthetic-source-revision',name='Example Candidate',title='Example Engineer',
            source_path='approved.txt',pdf_path='approved.pdf',source_sha256=hashlib.sha256(self.source.read_bytes()).hexdigest(),
            pdf_sha256=hashlib.sha256(self.pdf.read_bytes()).hexdigest(),required_pages=1,
            sections=['Experience','Education'],required_entries=['Example Candidate','Example Engineering'],
            employment_provenance=[dict(evidence_ids=['synthetic-evidence'])],coverage={'synthetic':True})
        self.path=self.root/'manifest.json';self.save()
        env=patch.dict(os.environ,{'JOB_RESUME_MANIFEST':str(self.path)});env.start();self.addCleanup(env.stop)
        self.candidate=dict(commit='synthetic-source-revision',evidence=[{'id':'synthetic-evidence'}],needs_input=[])
    def save(self):self.path.write_text(json.dumps(self.manifest))
    def test_unconfigured_manifest_holds(self):
        with patch('workflow.runtime_config.config',return_value={}),patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'resume_manifest'):verified_manifest()
    def test_dynamic_variants_are_rejected(self):
        for variant in ['ai','platform']:
            with self.assertRaisesRegex(ValueError,'disabled'):write({},variant)
    def test_reuse_is_byte_identical(self):
        m=verified_manifest();out=self.root/'copy.pdf';render(dict(variant='master',pdf_sha256=m['pdf_sha256']),out)
        self.assertEqual(out.read_bytes(),self.pdf.read_bytes());self.assertEqual(out.with_suffix('.txt').read_bytes(),self.source.read_bytes())
    def test_substituted_artifact_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'canonical'):render(dict(variant='master',pdf_sha256='wrong'),self.root/'unused.pdf')
    def test_source_and_pdf_are_pinned(self):
        m=verified_manifest();self.assertEqual(m['pdf_sha256'],hashlib.sha256(self.pdf.read_bytes()).hexdigest())
    def test_missing_or_invalid_hash_is_rejected(self):
        original=copy.deepcopy(self.manifest)
        for key in ('source_sha256','pdf_sha256'):
            for v in (None,'not-a-sha256'):
                with self.subTest(key=key,value=v):
                    self.manifest=copy.deepcopy(original);self.manifest[key]=v;self.save()
                    with self.assertRaisesRegex(ValueError,'missing or invalid hash'):verified_manifest()
    def test_changed_bytes_are_rejected(self):
        self.pdf.write_bytes(self.pdf.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'Master artifact changed'):verified_manifest()
    def test_candidate_pin_is_required(self):
        self.candidate['commit']='different'
        with self.assertRaisesRegex(ValueError,'Candidate sources changed'):write(self.candidate)
    def test_required_evidence_is_preserved(self):
        self.candidate['evidence']=[]
        with self.assertRaisesRegex(ValueError,'employment evidence is missing'):write(self.candidate)
    def test_content_review_remains_held_for_semantic_review(self):
        doc=write(self.candidate);result=review(doc,self.candidate,self.pdf)
        self.assertTrue(result['passed'],result['issues']);self.assertTrue(doc['held']);self.assertFalse(result['semantic_passed'])
    def test_changed_metadata_fails(self):
        doc=write(self.candidate);doc['title']='Different title'
        self.assertIn('master_metadata_changed:title',review(doc,self.candidate,self.pdf)['issues'])
    def test_missing_entry_fails(self):
        from pypdf import PdfReader
        page=Mock();page.extract_text.return_value=PdfReader(self.pdf).pages[0].extract_text().replace('Example Engineering','')
        with patch('pypdf.PdfReader',return_value=Mock(pages=[page])):result=review(write(self.candidate),self.candidate,self.pdf)
        self.assertIn('coverage_missing:Example Engineering',result['issues'])
