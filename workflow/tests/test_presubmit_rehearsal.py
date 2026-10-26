"""Synthetic pre-submit fixtures: no real browser or network access."""
import time,unittest
from unittest.mock import patch
from workflow.engine import Held
from workflow.pilot import Pilot
from workflow.tests import test_pilot

class PresubmitRehearsalTests(unittest.TestCase):
 setUp=test_pilot.PilotTests.setUp
 tearDown=test_pilot.PilotTests.tearDown
 bind_grant=test_pilot.PilotTests.bind_grant
 prepared=test_pilot.PilotTests.prepared
 reviewed=test_pilot.PilotTests.reviewed
 def rehearsal(self):
  self.grant.pop('approved_by');self.grant.pop('confirmation');self.grant.pop('submit_selector')
  self.grant['allow_submit']=False;self.bind_grant()
  self.pilot=Pilot(self.p,self.b,fixture=True,rehearsal=True)
 def test_complete_review_without_submission_approval(self):
  self.rehearsal();aid,approval=self.reviewed();a=self.pilot.current('synthetic',aid)[2]
  self.assertEqual(a['mode'],'presubmit_rehearsal');self.assertEqual(a['status'],'form_reviewed')
  self.assertEqual(a['review_evidence']['pdf_sha256'],self.hash)
  self.assertIsNotNone(self.p.store.read()[1]['browser']);self.assertEqual(self.b.clicks,0)
  self.assertRaises(Held,self.pilot.submit,'synthetic',aid,approval)
  self.assertRaises(Held,self.pilot.confirm,'synthetic',aid)
  self.assertRaises(Held,self.pilot.record,'synthetic',aid,{'form_reviewed'},'submit_pending')
  self.assertRaises(Held,self.pilot.record,'synthetic',aid,{'form_reviewed'},'confirmed')
  self.assertEqual(self.b.clicks,0)
 def test_supervised_pilot_cannot_take_over_rehearsal(self):
  self.rehearsal();aid,approval=self.reviewed();other=Pilot(self.p,self.b,fixture=True)
  self.assertRaises(Held,other.submit,'synthetic',aid,approval)
  self.assertRaises(Held,other.record,'synthetic',aid,{'form_reviewed'},'opened')
  self.assertRaises(Held,self.pilot.record,'synthetic',aid,{'form_reviewed'},'opened',mode='fixture')
  self.assertEqual(self.pilot.current('synthetic',aid)[2]['mode'],'presubmit_rehearsal')
 def test_rehearsal_pilot_cannot_take_over_supervised_attempt(self):
  aid=self.prepared();other=Pilot(self.p,self.b,fixture=True,rehearsal=True)
  self.assertRaises(Held,other.open,'synthetic',aid);self.assertEqual(self.b.location,'')
 def test_unknown_required_question_holds_and_never_replays(self):
  self.rehearsal();aid=self.prepared();self.pilot.open('synthetic',aid)
  with patch.object(self.b,'required_fields',return_value=['#name','#resume','#unknown']):
   self.assertRaises(Held,self.pilot.fill,'synthetic',aid,'name')
  self.assertEqual(self.pilot.current('synthetic',aid)[2]['status'],'uncertain')
  self.assertIsNotNone(self.p.store.read()[1]['browser'])
  self.assertRaises(Held,self.pilot.fill,'synthetic',aid,'name');self.assertEqual(self.b.values,{})
 def test_upload_response_failure_retains_ownership(self):
  self.rehearsal();aid=self.prepared();self.pilot.open('synthetic',aid);self.pilot.fill('synthetic',aid,'name')
  with patch.object(self.b,'uploaded',side_effect=RuntimeError('response lost')):
   self.assertRaises(RuntimeError,self.pilot.upload,'synthetic',aid)
  self.assertEqual(self.pilot.current('synthetic',aid)[2]['status'],'uncertain')
  self.assertIsNotNone(self.p.store.read()[1]['browser']);self.assertRaises(Held,self.pilot.upload,'synthetic',aid)
 def test_unconfirmed_answer_and_submit_binding_rejected(self):
  self.rehearsal();self.grant['fields']['name']['value']='invented';self.bind_grant()
  self.assertRaises(Held,self.prepared)
 def test_generic_finish_cannot_settle_rehearsal(self):
  self.rehearsal();aid=self.prepared()
  for outcome in ('dry_run_complete','confirmed','submitted','uncertain'):
   self.assertRaises(Held,self.p.finish,'synthetic',aid,outcome,'evidence')
  self.assertEqual(self.pilot.current('synthetic',aid)[2]['status'],'prepared')
  self.assertIsNotNone(self.p.store.read()[1]['browser'])
 def test_explicit_submit_permission_rejected(self):
  self.rehearsal();self.grant['allow_submit']=True;self.bind_grant();self.assertRaises(Held,self.prepared)
 def test_fresh_reservation_required_during_rehearsal(self):
  self.rehearsal();aid=self.prepared()
  with patch('workflow.mail_sync.require_reservation',side_effect=Held('stale receipt check')):
   self.assertRaises(Held,self.pilot.open,'synthetic',aid)
  self.assertEqual(self.b.location,'')
 def test_live_rehearsal_requires_explicit_mode_environment(self):
  self.rehearsal();self.grant['url']='https://www.linkedin.com/jobs/view/123/'
  live=Pilot(self.p,self.b,rehearsal=True)
  with patch.dict('os.environ',{'JOB_PIPELINE_ENABLE_LIVE':'supervised'},clear=True):
   self.assertRaises(Held,live.enabled,self.grant['url'])
  with patch.dict('os.environ',{'JOB_PIPELINE_ENABLE_LIVE':'rehearsal'},clear=True):
   self.assertRaises(Held,live.enabled,self.grant['url'])
   with patch('workflow.runtime_config.require_operational_context',return_value=None):live.enabled(self.grant['url'])
  self.assertEqual(self.b.location,'')

if __name__=='__main__':unittest.main()
