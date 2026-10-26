import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from workflow.engine import Held,LocalStore,Pipeline,require_application_route
from workflow.discovery import discover,Page
from workflow.mail_sync import require_reservation,reserve

class RouteTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=LocalStore(str(Path(self.tmp.name)/'state.sqlite'));self.p=Pipeline(self.store)
  self.p.ingest([dict(id='j',company='Example',title='Product',url='https://www.linkedin.com/jobs/view/123/')])
  self.p.mutate('scope',lambda s:s.update(paused=False,submission_controls={'application_route_policy':'linkedin_easy_apply_only'}))
 def tearDown(self):self.tmp.cleanup()
 def verify(self):
  self.p.mutate('route',lambda s:s['jobs']['j'].update(application_route=dict(kind='linkedin_easy_apply',verified=True,label='Easy Apply',checked_at='2026-09-26',source_url='https://www.linkedin.com/jobs/view/123/')))
 def test_unknown_listing_cannot_claim_reserve_or_submit(self):
  with self.assertRaises(Held):self.p.claim('j','worker')
  with self.assertRaises(Held):reserve(self.store,'j','worker',{})
  with self.assertRaises(Held):require_reservation(self.store.read()[1],'j')
 def test_verified_route_claims_but_external_target_stays_held(self):
  self.verify();self.assertTrue(self.p.claim('j','worker'))
  s=self.store.read()[1];require_application_route(s,'j')
  with self.assertRaises(Held):require_application_route(s,'j','https://jobs.ashbyhq.com/example/123')
 def test_other_job_or_lookalike_host_is_not_route_evidence(self):
  self.verify()
  for url in ('https://www.linkedin.com/jobs/view/456/','https://evil-linkedin.com/jobs/view/123/'):
   s=copy.deepcopy(self.store.read()[1]);s['jobs']['j']['application_route']['source_url']=url
   with self.assertRaises(Held):require_application_route(s,'j')
 def test_discovery_requests_easy_apply_filter_without_claiming_route(self):
  urls=[]
  def fake(url):
   urls.append(url);p=Page();p.links=['https://www.linkedin.com/jobs/view/123/'];return p
  with patch('workflow.discovery.fetch',side_effect=fake):self.assertEqual(discover(),['https://www.linkedin.com/jobs/view/123'])
  self.assertTrue(urls)
  for u in urls:self.assertEqual(parse_qs(urlsplit(u).query)['f_AL'],['true'])
