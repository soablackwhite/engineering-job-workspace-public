"""Regressions for failures reproduced in the October 8 audit."""
import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from workflow.engine import Pipeline,LocalStore,Held,Conflict
from workflow.evidence import facts,render_fact_claim,validate_claims,current
from workflow.runner import prepare_jobs,release_claim
from workflow.safeguards import require_screen
from workflow.tests.fixtures import candidate,extraction,reviewed_screen

class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.p=Pipeline(LocalStore(self.root/'state.sqlite'));self.p.candidate(candidate(self.root,years=2));self.p.pause(False)
        self.p.ingest([dict(id='j',company='Fixture',title='Applied AI Engineer',url='https://example.test/j',
                           description='US junior. 2 years of professional experience. Sponsorship available.',checked_at='2026-10-08')])
    def tearDown(self):self.p.store.db.close();self.tmp.cleanup()
    def job(self):return self.p.store.read()[1]['jobs']['j']
    def output(self,job,ctx):return extraction(job,{'minimum_years':2})
    def review(self,job,ctx):return extraction(job,{'minimum_years':2})
    def draft(self,j,c):return dict(writer='fixture',variant='master',pdf_sha256='fixture-pdf')
    def run_jobs(self,**kwargs):
        return prepare_jobs(self.p,['j'],kwargs.pop('extract',self.output),kwargs.pop('draft',self.draft),
                            reviewer=kwargs.pop('reviewer',self.review),reviewer_id='trusted-second-adapter',**kwargs)
    def test_fabricated_reviewer_cannot_approve(self):
        token=self.p.claim('j','writer');data=self.output(self.job(),{})
        self.assertIn('independent_requirement_capture_review',self.p.screen('j',token,data)['questions'])
    def test_independent_numeric_lie_cannot_approve(self):
        token=self.p.claim('j','writer');data=self.output(self.job(),{});data['observations']['minimum_years']['value']=0
        self.assertFalse(self.p.attest_capture('j',token,data,copy.deepcopy(data),'trusted-second-adapter'))
        self.assertNotEqual(self.p.screen('j',token,data)['decision'],'eligible')
    def test_reviewer_disagreement_blocks_drafting(self):
        draft=Mock()
        result=self.run_jobs(draft=draft,reviewer=lambda j,c:extraction(j,{'minimum_years':5}))
        draft.assert_not_called();self.assertIn('independent_requirement_capture_review',result['jobs'][0]['screen']['questions'])
    def test_reviewer_receives_no_first_answer_or_candidate(self):
        def review(job,ctx):
            self.assertEqual(ctx,{});self.assertNotIn('extraction_cache',job);self.assertNotIn('screen',job)
            return self.review(job,ctx)
        self.assertEqual(self.run_jobs(reviewer=review)['jobs'][0]['state'],'drafted')
    def test_template_claim_preserves_scope_and_value(self):
        index=self.p.store.read()[1]['candidate']['evidence_index'];key='skill.Python.years'
        claim=dict(template='skill_years_v1',fact_key=key,text=render_fact_claim(index,key),evidence_ids=facts(index)[key]['evidence_ids'])
        self.assertFalse(validate_claims(index,[claim]))
        for false in ['I have 4 years of Python experience.','I have 2 years of Python leadership experience.','I have 2 years of PyTorch experience.']:
            self.assertTrue(validate_claims(index,[dict(claim,text=false)]))
    def test_irrelevant_edit_reuses_doc_relevant_edit_rechecks_without_extracting(self):
        extract=Mock(side_effect=self.output);draft=Mock(side_effect=self.draft)
        first=self.run_jobs(extract=extract,draft=draft);release_claim(self.p,'j',first['jobs'][0]['claim_token'])
        file=self.root/'vault/facts.md';file.write_text(file.read_text()+'\n\nWriting style: be concise.')
        self.p.evidence_refresh(str(file.parent),['facts.md'],str(self.root/'cache.sqlite'))
        second=self.run_jobs(extract=extract,draft=draft);self.assertEqual(second['metrics']['document_reuse'],1)
        release_claim(self.p,'j',second['jobs'][0]['claim_token'])
        file.write_text(file.read_text().replace('professional_years: 2','professional_years: 1'))
        self.p.evidence_refresh(str(file.parent),['facts.md'],str(self.root/'cache.sqlite'))
        third=self.run_jobs(extract=extract,draft=draft)
        self.assertEqual(third['jobs'][0]['screen']['decision'],'excluded');self.assertEqual(extract.call_count,1);self.assertEqual(draft.call_count,1)
    def test_same_size_change_and_new_conflict_detected_without_refresh(self):
        first=self.run_jobs();state=self.p.store.read()[1];file=self.root/'vault/facts.md'
        original=file.read_text();file.write_text(original.replace('professional_years: 2','professional_years: 1'))
        with self.assertRaises(Held):require_screen(state,state['jobs']['j'])
        file.write_text(original+'\n\nprofessional_years: 5')
        with self.assertRaises(Held):require_screen(state,state['jobs']['j'])
    def test_deadline_returns_and_late_result_cannot_write(self):
        release=threading.Event();started=threading.Event()
        def hung(job,ctx):started.set();release.wait(2);return self.output(job,ctx)
        try:
            before=time.monotonic();result=self.run_jobs(extract=hung,stage_timeout=.03,heartbeat_interval=.01)
            self.assertLess(time.monotonic()-before,.5);self.assertTrue(started.is_set())
            self.assertIn('deadline exceeded',result['jobs'][0]['held'])
            rev,state=self.p.store.read();self.assertEqual(state['jobs']['j']['preparation']['status'],'uncertain')
            self.assertGreater(len(state['jobs']['j']['preparation_history']),2)
            release.set();time.sleep(.02);self.assertEqual(self.p.store.read()[0],rev)
            result=self.run_jobs();self.assertIn('reconciliation',result['jobs'][0]['held'])
        finally:release.set()
    def test_metrics_visible_before_batch_finishes(self):
        def draft(job,ctx):
            store=LocalStore(self.root/'state.sqlite')
            try:history=store.read()[1]['jobs']['j']['preparation_history']
            finally:store.db.close()
            self.assertTrue(any(e['stage']=='extract' and e['status']=='complete' for e in history))
            return self.draft(job,ctx)
        self.assertEqual(self.run_jobs(draft=draft)['jobs'][0]['state'],'drafted')
    def test_handoff_failure_retains_one_result_and_no_false_zero(self):
        result=self.run_jobs(on_ready=Mock(side_effect=TimeoutError('Consumer uncertainty')))
        self.assertEqual(len(result['jobs']),1);self.assertIsNone(result['external_submissions'])
        self.assertEqual(self.job()['preparation']['status'],'uncertain')
    def test_metadata_cas_retry_does_not_repeat_extractor(self):
        original=self.p.mutate;failed=[];extract=Mock(side_effect=self.output)
        def mutate(action,fn):
            if action=='preparation_complete' and not failed:failed.append(True);raise Conflict('fixture concurrent edit')
            return original(action,fn)
        with patch.object(self.p,'mutate',side_effect=mutate):
            result=self.run_jobs(extract=extract)
        self.assertEqual(result['jobs'][0]['state'],'drafted');self.assertEqual(extract.call_count,1)

    def test_cheap_filter_skips_only_explicit_mandatory_tenure(self):
        from workflow.qualification import triage
        job=self.job();candidate=self.p.store.read()[1]['candidate']
        for text,excluded in [('5+ years of professional software engineering experience.',True),
                              ('3+ years in strong product engineering environments',True),
                              ('Typically 5+ years of software engineering experience, or equivalent ownership.',False),
                              ('Preferred Experience\n5+ years of software engineering experience.',False),
                              ('Our company has 20 years of experience.',False),
                              ('0–5 years of software engineering experience, open to new grads',False)]:
            job['snapshots'][-1]['text']=text
            self.assertEqual(bool(triage(job,candidate)),excluded,text)

    def test_description_change_during_callback_cannot_be_attested(self):
        def extract(job,ctx):
            other=Pipeline(LocalStore(self.root/'state.sqlite'))
            try:other.ingest([dict(id='j',company='Fixture',title='Applied AI Engineer',url='https://example.test/j',
                                  description='3 years. Additional mandatory requirement.',checked_at='2026-10-08')])
            finally:other.store.db.close()
            return self.output(job,ctx)
        draft=Mock();result=self.run_jobs(extract=extract,draft=draft)
        self.assertIn('Job changed',result['jobs'][0]['held']);draft.assert_not_called()
        self.assertNotIn('capture_attestation',self.job())

    def test_different_valid_quote_spans_do_not_cause_false_review_hold(self):
        token=self.p.claim('j','writer');original=self.output(self.job(),{})
        independent=copy.deepcopy(original)
        independent['observations']['minimum_years']['quote']='2 years of professional experience'
        self.assertTrue(self.p.attest_capture('j',token,original,independent,'trusted-second-adapter'))
        self.assertEqual(self.p.screen('j',token,original)['decision'],'eligible')
        independent['observations']['minimum_years']['quote']='2 years of imaginary experience'
        self.assertFalse(self.p.attest_capture('j',token,original,independent,'trusted-second-adapter'))
