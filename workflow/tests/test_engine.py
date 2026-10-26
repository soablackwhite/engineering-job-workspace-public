import copy,tempfile,unittest
from pathlib import Path
from workflow.engine import Pipeline,LocalStore,Held,Conflict,canonical
from workflow.tests.fixtures import reviewed_screen, candidate, extraction

class EngineTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'app.sqlite';self.store=LocalStore(self.path);self.p=Pipeline(self.store)
  self.row=dict(id='stable',company='Example',title='Engineer',url='https://www.linkedin.com/jobs/view/123?tracking=abc',description='US entry role. Three years. Sponsorship available. Python.',checked_at='2026-09-13T09:00:00Z',tracking={'notes':'Keep me','status':'Interested'})
  self.p.ingest([self.row]);self.p.candidate(candidate(Path(self.tmp.name)));self.p.pause(False);self.token=self.p.claim('stable','writer')
 def tearDown(self):self.tmp.cleanup()
 def facts(self):return extraction(self.store.read()[1]['jobs']['stable'])
 def doc(self,**kw):return dict(writer='writer',variant='master',pdf_sha256='fixture-pdf',**kw)
 def ready(self):
  reviewed_screen(self.p,'stable',self.token,self.facts());self.p.document('stable',self.token,self.doc());self.p.review('stable',self.token,'independent',{'passed':True,'semantic_passed':True})
 def test_same_url_dedup_and_tracking_preserved(self):
  self.p.ingest([dict(self.row,id='different',tracking={'notes':'overwrite'})]);j=self.store.read()[1]['jobs'];self.assertEqual(len(j),1);self.assertEqual(j['stable']['tracking']['notes'],'Keep me')
 def test_cross_source_requisition_dedup(self):
  row=dict(self.row,requisition_id='r1');self.p.ingest([row]);self.p.ingest([dict(row,id='other',url='https://company.example/careers/r1')]);self.assertEqual(len(self.store.read()[1]['jobs']),1)
 def test_same_title_different_requisitions_flagged(self):
  self.p.ingest([dict(self.row,id='other',url='https://company.example/job/2')]);self.assertEqual(self.store.read()[1]['jobs']['other']['possible_duplicates'],['stable'])
 def test_stale_revision(self):
  rev,s=self.store.read();self.p.pause();self.assertRaises(Conflict,self.store.save,rev,s)
 def test_claim_collision(self):self.assertRaises(Held,self.p.claim,'stable','other')
 def test_stale_claim(self):self.assertRaises(Held,self.p.screen,'stable','invalid',self.facts())
 def test_expired_claim(self):
  self.p.mutate('expire',lambda s:s['jobs']['stable']['claim'].update(expires=0));self.assertRaises(Held,self.p.screen,'stable',self.token,self.facts())
 def test_pause_blocks_new_claim(self):
  self.p.pause();self.assertRaises(Held,self.p.claim,'stable','other')
 def test_missing_quotes_hold(self):
  facts=self.facts();facts.pop('capture_review');self.assertEqual(reviewed_screen(self.p,'stable',self.token,facts)['decision'],'needs_input')
 def test_writer_cannot_self_review(self):
  reviewed_screen(self.p,'stable',self.token,self.facts());self.p.document('stable',self.token,self.doc());self.assertRaises(Held,self.p.review,'stable',self.token,'writer',{'passed':True})
 def test_revision_cap(self):
  reviewed_screen(self.p,'stable',self.token,self.facts())
  for i in range(2):
   self.p.document('stable',self.token,self.doc(version=i));self.p.review('stable',self.token,'reviewer',{'passed':False,'issues':['specific issue']})
   if i==1:self.assertRaises(Held,self.p.revision,'stable',self.token)
   self.p.revision('stable',self.token,'specific issue' if i else None)
  self.p.document('stable',self.token,self.doc(version=3));self.p.review('stable',self.token,'reviewer',{'passed':False});self.assertRaises(Held,self.p.revision,'stable',self.token,'specific issue')
 def test_unknown_attempt_not_retried(self):
  self.ready();attempt=self.p.prepare('stable',self.token);self.p.finish('stable',attempt,'uncertain','Interrupted before observing outcome');self.assertRaises(Held,self.p.prepare,'stable',self.token);self.assertIsNotNone(self.store.read()[1]['browser']);self.p.reconcile('stable','Verified no external action in dry-run adapter');self.assertIsNone(self.store.read()[1]['browser'])
 def test_live_outcome_forbidden(self):
  self.ready();attempt=self.p.prepare('stable',self.token);self.assertRaises(Held,self.p.finish,'stable',attempt,'confirmed','x')
 def test_prepared_attempt_survives_restart(self):
  self.ready();attempt=self.p.prepare('stable',self.token);other=Pipeline(LocalStore(self.path));self.assertEqual(other.store.read()[1]['jobs']['stable']['attempts'][0]['id'],attempt)
 def test_dry_run_is_terminal_and_preserves_tracking(self):
  self.ready();attempt=self.p.prepare('stable',self.token);self.p.finish('stable',attempt,'dry_run_complete','Local plan only');self.p.ingest([self.row]);self.assertEqual(self.store.read()[1]['jobs']['stable']['state'],'dry_run_complete')
 def test_single_browser_owner(self):
  self.ready();self.p.prepare('stable',self.token)
  row=dict(self.row,id='second',url='https://example.com/job/2');self.p.ingest([row]);t=self.p.claim('second','other');reviewed_screen(self.p,'second',t,extraction(self.store.read()[1]['jobs']['second']));self.p.document('second',t,dict(self.doc(),writer='other'));self.p.review('second',t,'reviewer',{'passed':True,'semantic_passed':True});self.assertRaises(Held,self.p.prepare,'second',t)
 def test_candidate_unknown_does_not_prepare(self):
  self.p.candidate({});reviewed_screen(self.p,'stable',self.token,self.facts());self.assertRaises(Held,self.p.document,'stable',self.token,self.doc());self.assertRaises(Held,self.p.prepare,'stable',self.token)
 def test_capacity_hold_preserves_revision_and_data(self):
  rev,before=self.store.read();large=copy.deepcopy(before);large['overflow']='x'*1500001;self.assertRaises(Held,self.store.save,rev,large);self.assertEqual(self.store.read(),(rev,before))
 def test_candidate_change_invalidates_screen(self):
  self.ready();self.p.candidate({'verified_engineering_years':1});self.assertRaises(Held,self.p.prepare,'stable',self.token)
 def test_skill_name_not_matched_inside_governance(self):
  self.p.candidate({'evidence':[{'text':'governance'}]});facts=self.facts();facts['required_skills']=['Go'];self.assertEqual(reviewed_screen(self.p,'stable',self.token,facts)['decision'],'needs_input')
 def test_application_adapter_only_creates_local_plan(self):
  from workflow.application import dry_run
  self.ready();plan=dry_run(self.p,'stable',self.token,Path(self.tmp.name)/'plan.json');self.assertEqual(plan['external_actions'],[]);self.assertFalse(plan['final_submit_enabled'])
 def test_linkedin_slug_normalization(self):self.assertEqual(canonical('https://www.linkedin.com/jobs/view/test-engineer-123/?x=1'),'https://www.linkedin.com/jobs/view/123')

 def test_master_policy_rejects_other_pdf_without_mutation(self):
  self.p.mutate('master',lambda s:s['candidate'].update(cv_policy='single_master_no_tailoring',master_pdf_sha256='fixture-pdf'))
  reviewed_screen(self.p,'stable',self.token,self.facts());before=self.store.read()
  self.assertRaises(Held,self.p.document,'stable',self.token,{'writer':'writer','pdf_sha256':'other'})
  self.assertEqual(self.store.read(),before)
  self.p.document('stable',self.token,self.doc())

 def test_candidate_reimport_preserves_application_context(self):
  context={'writing_preferences':{'default_edit_level':'light edits'},'confirmed_answers':{'salary':110000}}
  self.p.mutate('context',lambda s:s['candidate'].update(application_drafting_context=context))
  self.p.candidate({'verified_engineering_years':3})
  self.assertEqual(self.store.read()[1]['candidate']['application_drafting_context'],context)

if __name__=='__main__':unittest.main()
