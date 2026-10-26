import tempfile
import unittest
from pathlib import Path
from workflow.candidate import eligible
from workflow.engine import LocalStore, Pipeline
from workflow.tests.fixtures import reviewed_screen, candidate, extraction

class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.p=Pipeline(LocalStore(self.root/'state.sqlite'))
        self.p.ingest([dict(id='j',title='Applied AI Engineer',company='Synthetic',url='https://example.test/j',
            description='US entry 3 years. Sponsorship available. Python.',checked_at='2026-10-07')])
        self.p.candidate(candidate(self.root));self.p.pause(False);self.token=self.p.claim('j','test')
    def tearDown(self):self.tmp.cleanup()
    def screen(self,**overrides):return reviewed_screen(self.p,'j',self.token,extraction(self.p.store.read()[1]['jobs']['j'],overrides))
    def test_explicit_sponsorship_can_support_candidate_needing_visa(self):self.assertEqual(self.screen()['decision'],'eligible')
    def test_unknown_experience_cannot_pass(self):
        self.p.candidate({});self.assertEqual(self.screen()['decision'],'needs_input')
    def test_no_sponsorship_excluded_when_needed(self):self.assertEqual(self.screen(sponsorship='explicit_no')['decision'],'excluded')
    def test_senior_role_excluded(self):self.assertEqual(self.screen(seniority='senior')['decision'],'excluded')
    def test_insufficient_experience_excluded(self):self.assertEqual(self.screen(minimum_years=4)['decision'],'excluded')
    def test_historical_sponsorship_requires_review(self):self.assertEqual(self.screen(sponsorship='credible_history')['decision'],'needs_input')
    def test_legacy_aggregate_cannot_pass(self):self.assertEqual(eligible({'sponsorship':'explicit'}, {'verified_engineering_years':3})['decision'],'needs_input')
