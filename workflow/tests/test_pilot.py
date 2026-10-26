"""All identities, grants, reviews and forms in this file are SYNTHETIC fixtures."""
import copy,hashlib,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from workflow.engine import LocalStore,Pipeline,Held,sha
from workflow.pilot import Pilot
from workflow.tests.fixtures import reviewed_screen, candidate, extraction

class MockBrowser:
 def __init__(self):self.location='';self.values={};self.file='';self.clicks=0;self.bad_job=False
 def open(self,url):self.location=url
 def url(self):return self.location
 def label(self,f):return f['label']
 def fill(self,f,v):self.values[f['selector']]=v
 def value(self,f):return self.values.get(f['selector'])
 def upload(self,selector,path):self.file=path
 def uploaded(self,selector):return self.file
 def file_hash(self,selector):return hashlib.sha256(Path(self.file).read_bytes()).hexdigest()
 def required_fields(self):return ['#name','#resume']
 def snapshot(self):return {'fixture':True,'values':self.values.copy()}
 def click(self,selector):self.clicks+=1;self.location='http://127.0.0.1/complete'
 def text(self,selector):return {'#message':'LOCAL fixture received','#job':'wrong job' if self.bad_job else 'Synthetic job','#reference':'FIXTURE-001'}[selector]

class PilotTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);self.p=Pipeline(LocalStore(self.path/'fixture.sqlite'));self.b=MockBrowser()
  self.p.ingest([{'id':'synthetic','company':'Fixture only','title':'Synthetic job','url':'https://example.test/job','description':'US entry 3 years sponsorship available','checked_at':'2026-09-13T00:00:00Z'}])
  self.p.pause(False);self.token=self.p.claim('synthetic','fixture-writer',900)
  self.pdf=self.path/'fixture-only.pdf';self.pdf.write_bytes(b'%PDF-1.4\n% SYNTHETIC TEST FILE ONLY\n');self.hash=hashlib.sha256(self.pdf.read_bytes()).hexdigest()
  self.p.candidate(candidate(self.path,pdf_hash=self.hash))
  reviewed_screen(self.p,'synthetic',self.token,extraction(self.p.store.read()[1]['jobs']['synthetic']))
  docid=self.p.document('synthetic',self.token,{'writer':'fixture-writer','variant':'master','pdf_sha256':self.hash})
  self.p.review('synthetic',self.token,'fixture-reviewer',{'passed':True,'semantic_passed':True,'pdf_sha256':self.hash})
  self.grant={'job_id':'synthetic','approved_by':'user','expires_at':time.time()+600,'url':'http://127.0.0.1/form','document_id':docid,'snapshot_hash':self.p.store.read()[1]['jobs']['synthetic']['snapshots'][-1]['hash'],'pdf_sha256':self.hash,'fields':{'name':{'value':'SYNTHETIC CANDIDATE','kind':'text','selector':'#name','label':'Full name','label_selector':'#name-label'}},'answers_sha256':sha({'name':'SYNTHETIC CANDIDATE'}),'upload_selector':'#resume','submit_selector':'#submit','allow_submit':True,'confirmation':{'url':'http://127.0.0.1/complete','message_selector':'#message','message':'LOCAL fixture received','job_selector':'#job','job':'Synthetic job','reference_selector':'#reference'}}
  self.bind_grant()
  self.pilot=Pilot(self.p,self.b,fixture=True)
 def bind_grant(self):
  contract={k:v for k,v in self.grant.items() if k not in ('package_sha256','package_version')}
  bound=self.p.package('synthetic',{'name':'SYNTHETIC CANDIDATE'},contract)
  self.grant.update(package_sha256=bound['sha256'],package_version=bound['version'])
 def tearDown(self):self.tmp.cleanup()
 def prepared(self):return self.pilot.prepare('synthetic',self.token,self.grant,self.pdf)
 def reviewed(self):
  aid=self.prepared();self.pilot.open('synthetic',aid);self.pilot.fill('synthetic',aid,'name');self.pilot.upload('synthetic',aid);receipt=self.pilot.review('synthetic',aid);return aid,{'approved_by':'user','review_sha256':receipt['review_sha256'],'expires_at':time.time()+60}
 def test_positive_fixture_path(self):
  aid,approval=self.reviewed();self.pilot.submit('synthetic',aid,approval);self.pilot.confirm('synthetic',aid);self.assertEqual(self.b.clicks,1);self.assertEqual(self.pilot.current('synthetic',aid)[2]['status'],'confirmed')
 def test_native_profile_blocks_raw_bypass_tools(self):
  from workflow.hermes_plugin import guard_raw_tools
  for name in ('browser_click','browser_console','terminal','write_file'):self.assertEqual(guard_raw_tools(name)['action'],'block')
  self.assertIsNone(guard_raw_tools('job_application'))
 def test_live_is_disabled_before_browser_use(self):
  with patch.dict('os.environ',{},clear=True):self.assertRaises(Held,Pilot(self.p,self.b).prepare,'synthetic',self.token,self.grant,self.pdf)
  self.assertEqual(self.b.location,'')
 def test_fixture_cannot_target_external_host(self):
  self.grant['url']='https://www.linkedin.com/jobs';self.assertRaises(Held,self.prepared)
 def test_unreviewed_artifact_rejected(self):
  self.pdf.write_bytes(b'changed');self.assertRaises(Held,self.prepared)
 def test_recorded_answer_binding(self):
  self.grant['fields']['name']['value']='invented';self.assertRaises(Held,self.prepared)
 def test_skip_upload_rejected(self):
  aid=self.prepared();self.assertRaises(Held,self.pilot.review,'synthetic',aid)
 def test_approval_of_another_review_rejected(self):
  aid,approval=self.reviewed();approval['review_sha256']='wrong';self.assertRaises(Held,self.pilot.submit,'synthetic',aid,approval);self.assertEqual(self.b.clicks,0)
 def test_duplicate_submit_rejected(self):
  aid,approval=self.reviewed();self.pilot.submit('synthetic',aid,approval);self.assertRaises(Held,self.pilot.submit,'synthetic',aid,approval);self.assertEqual(self.b.clicks,1)
 def test_wrong_job_confirmation_becomes_uncertain(self):
  aid,approval=self.reviewed();self.pilot.submit('synthetic',aid,approval);self.b.bad_job=True;self.assertRaises(Held,self.pilot.confirm,'synthetic',aid);self.assertEqual(self.pilot.current('synthetic',aid)[2]['status'],'uncertain');self.assertIsNotNone(self.p.store.read()[1]['browser'])
 def test_click_failure_retains_lease_and_cannot_retry(self):
  aid,approval=self.reviewed()
  with patch.object(self.b,'click',side_effect=RuntimeError('lost response')):self.assertRaises(RuntimeError,self.pilot.submit,'synthetic',aid,approval)
  self.assertEqual(self.pilot.current('synthetic',aid)[2]['status'],'uncertain');self.assertRaises(Held,self.pilot.submit,'synthetic',aid,approval)
 def test_pending_click_crash_requires_reconciliation(self):
  aid,approval=self.reviewed();self.pilot.record('synthetic',aid,{'form_reviewed'},'submit_pending');self.assertRaises(Held,self.pilot.submit,'synthetic',aid,approval);self.assertEqual(self.b.clicks,0)
 def test_pause_prevents_side_effect(self):
  aid=self.prepared();self.p.pause();self.assertRaises(Held,self.pilot.open,'synthetic',aid);self.assertEqual(self.b.location,'')
 def test_reconciliation_confirms_without_reclick_after_pause(self):
  aid,approval=self.reviewed();self.pilot.submit('synthetic',aid,approval);self.pilot.uncertain('synthetic',aid,'Lost save');self.p.pause();self.pilot.reconcile('synthetic',aid);self.assertEqual(self.b.clicks,1);self.assertEqual(self.pilot.current('synthetic',aid)[2]['status'],'confirmed')
 def test_same_filename_changed_attachment_rejected(self):
  aid,approval=self.reviewed();self.pdf.write_bytes(b'changed bytes same filename');self.assertRaises(Held,self.pilot.submit,'synthetic',aid,approval);self.assertEqual(self.b.clicks,0)
 def test_changed_form_value_blocks_submit(self):
  aid,approval=self.reviewed();self.b.values['#name']='changed';self.assertRaises(Held,self.pilot.submit,'synthetic',aid,approval);self.assertEqual(self.b.clicks,0)

if __name__=='__main__':unittest.main()
