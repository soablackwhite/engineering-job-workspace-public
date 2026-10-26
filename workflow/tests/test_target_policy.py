import copy
from datetime import datetime, timedelta, timezone
import hashlib
import tempfile
import time
import unittest
from pathlib import Path

from workflow.engine import Held, LocalStore, Pipeline, sha
from workflow.mail_sync import reserve, require_reservation
from workflow.target_policy import VERSION, policy_digest, require_target_policy

CV = '2'*64
NOW = datetime.now(timezone.utc)
AT = NOW.isoformat()


def policy():
    value = dict(version=VERSION, enabled=True,
        allowed_role_families=['forward_deployed_engineer', 'solutions_engineer'],
        exclude_junior=True, allowed_countries=['US', 'GB'], gb_location='London',
        minimum_annual_usd=110000, compensation_basis='base', require_new_visa_sponsorship=True,
        require_abroad_applicants=True, require_relocation_assistance=True,
        max_evidence_age_days=30, current_cv_sha256=CV)
    value['digest'] = policy_digest(value)
    return value


def fixture():
    text = ('Solutions Engineer, mid level, United States. 2 years software engineering. '
            'We sponsor new work visas and welcome applicants currently abroad. '
            'Employer relocation assistance is available. Existing US authorization '
            'is not required before applying. Annual base salary USD100000-150000.')
    snapshot = dict(text=text, source_url='https://www.linkedin.com/jobs/view/123',
                    checked_at=AT, hash=sha(text))
    p = policy()
    values = dict(role_family='solutions_engineer', seniority='mid', country='US',
                  sponsorship='new_visa_available', abroad_applicants='accepted',
                  relocation_assistance='available', existing_authorization_required=False,
                  compensation=dict(minimum=100000, maximum=150000, currency='USD', period='annual', basis='base'))
    observations = {k:dict(value=v, evidence=[dict(source_id='role_snapshot', quote=text)]) for k,v in values.items()}
    job = dict(id='a', company='Example', title='Solutions Engineer', sources=[snapshot['source_url']],
               snapshots=[snapshot], attempts=[], target_policy_review=dict(
                   job_id='a', decision='eligible', reviewer='independent-reviewer', checked_at=AT,
                   policy_hash=p['digest'], snapshot_hash=snapshot['hash'], cv_sha256=CV,
                   source_hashes={'role_snapshot':snapshot['hash']}, observations=observations,
                   qualification_review=dict(passed=True, reviewer='qualification-reviewer',
                       snapshot_hash=snapshot['hash'], candidate_snapshot_sha256='candidate-current')))
    state = dict(paused=False, events=[], application_selection_policy=p, jobs={'a':job},
                 cloud_execution={'knowledge_snapshot':{'sha256':'candidate-current'}},
                 submission_controls=dict(audit_status='complete', gmail_crosscheck_required=False,
                   receipt_check_policy='crm_linkedin', gmail_requirement_waiver=dict(
                     source='direct_user_statement', quote='Gmail optional', at=AT)))
    return state


class TargetPolicyTests(unittest.TestCase):
    def setUp(self):
        self.state=fixture()

    def observation(self,key,value):
        self.state['jobs']['a']['target_policy_review']['observations'][key]['value']=value

    def held(self):
        with self.assertRaises(Held):require_target_policy(self.state,'a',now=NOW)

    def test_no_policy_preserves_legacy_behavior(self):
        self.assertIsNone(require_target_policy({'paused':True},'no-job'))

    def test_valid_range_overlapping_floor_passes(self):
        result=require_target_policy(self.state,'a',now=NOW)
        self.assertEqual(result['annual_usd_maximum'],150000)
        self.assertEqual(result['role_family'],'solutions_engineer')

    def test_pause_cannot_be_overridden_by_old_wrapup_grant(self):
        self.state['paused']=True
        self.state['submission_controls']['draft_wrapup_authorization']=dict(
            source='old grant',job_ids=['a'],expires_at=time.time()+1000)
        self.held()

    def test_missing_abroad_conditional_history_transfer_and_no_sponsor_hold(self):
        for key,value in [('abroad_applicants','unknown'),('abroad_applicants','excluded'),
                          ('sponsorship','credible_history'),('sponsorship','transfer_only'),
                          ('sponsorship','unavailable'),('existing_authorization_required',True),
                          ('relocation_assistance','unknown')]:
            with self.subTest(key=key,value=value):
                self.state=fixture();self.observation(key,value);self.held()
        self.state=fixture()
        self.state['jobs']['a']['target_policy_review']['observations']['abroad_applicants']['conditional']=True
        self.held()

    def test_wrong_family_and_junior_titles_hold(self):
        for title in ['AI Engineer','Product Engineer','Forward Deployed Analyst',
                      'Junior Solutions Engineer','Graduate FDE','FDE Intern']:
            with self.subTest(title=title):
                self.state=fixture();self.state['jobs']['a']['title']=title;self.held()
        self.state=fixture();self.observation('role_family','applied_ai');self.held()

    def test_london_only_and_no_current_auth_exclusion(self):
        self.observation('country','CA');self.held()
        self.state=fixture();self.observation('country','GB')
        obs=self.state['jobs']['a']['target_policy_review']['observations']
        obs['city']=copy.deepcopy(obs['country']);obs['city']['value']='Manchester';self.held()
        obs['city']['value']='London'
        self.assertEqual(require_target_policy(self.state,'a',now=NOW)['country'],'GB')

    def test_stale_policy_snapshot_cv_candidate_and_source_hold(self):
        mutations=[lambda s:s['application_selection_policy'].update(minimum_annual_usd=1),
                   lambda s:s['jobs']['a']['target_policy_review'].update(job_id='other-job'),
                   lambda s:s['jobs']['a']['snapshots'][-1].update(hash='changed'),
                   lambda s:s['jobs']['a']['snapshots'][-1].update(text='changed'),
                   lambda s:s['jobs']['a']['target_policy_review'].update(cv_sha256='3'*64),
                   lambda s:s['cloud_execution']['knowledge_snapshot'].update(sha256='new'),
                   lambda s:s['jobs']['a']['target_policy_review'].update(checked_at=(NOW-timedelta(days=31)).isoformat()),
                   lambda s:s['jobs']['a']['target_policy_review']['source_hashes'].update(role_snapshot='changed')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.state=fixture();mutate(self.state);self.held()

    def test_current_cv_change_requires_new_review(self):
        p=self.state['application_selection_policy'];p['current_cv_sha256']='4'*64;p['digest']=policy_digest(p)
        self.state['jobs']['a']['target_policy_review']['policy_hash']=p['digest'];self.held()

    def test_malformed_installed_proof_is_a_hold(self):
        mutations=[lambda s:s.update(application_selection_policy=None),
                   lambda s:s['application_selection_policy'].update(enabled=False),
                   lambda s:s['application_selection_policy'].update(current_cv_sha256=None),
                   lambda s:s['jobs']['a'].update(snapshots=['invalid']),
                   lambda s:s['jobs']['a'].update(target_policy_review='invalid'),
                   lambda s:s['jobs']['a'].update(target_policy_sources=None),
                   lambda s:s['jobs']['a']['target_policy_review'].update(observations='invalid'),
                   lambda s:s['jobs']['a']['target_policy_review'].update(source_hashes='invalid')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.state=fixture();mutate(self.state);self.held()

    def test_pay_below_floor_and_unknown_basis_hold(self):
        pay=dict(minimum=80000,maximum=109999,currency='USD',period='annual',basis='base')
        self.observation('compensation',pay);self.held()
        pay.update(maximum=150000,basis='estimated');self.held()

    def test_ote_total_cash_and_unannualized_contract_do_not_meet_base_floor(self):
        for basis in ['ote','total_cash','bonus','equity']:
            with self.subTest(basis=basis):
                self.state=fixture()
                self.observation('compensation',dict(minimum=150000,maximum=250000,
                    currency='USD',period='annual',basis=basis));self.held()
        self.state=fixture()
        self.observation('compensation',dict(minimum=70,maximum=100,
            currency='USD',period='hourly',basis='base'));self.held()

    def test_company_policy_is_bound_to_exact_role_and_immutable_capture(self):
        j=self.state['jobs']['a'];text='All US roles sponsor new visas and accept international applicants.'
        source=dict(id='company',kind='employer_policy',scope='company',job_ids=['a'],
                    url='https://example.com/careers',text=text,checked_at=AT,
                    hash=hashlib.sha256(text.encode()).hexdigest())
        j['target_policy_sources']=[source];r=j['target_policy_review'];r['source_hashes']['company']=source['hash']
        for key in ['sponsorship','abroad_applicants']:
            r['observations'][key]['evidence']=[dict(source_id='company',quote=text)]
        require_target_policy(self.state,'a',now=NOW)
        source['job_ids']=[];self.held();source['job_ids']=['a'];source['text']+=' changed';self.held()

    def test_gbp_base_range_requires_primary_fx_and_reaches_usd_floor(self):
        j=self.state['jobs']['a'];text='Synthetic primary quote: 1 GBP = 1.3363 USD.'
        source=dict(id='fx1',kind='primary_exchange_rate',scope='fx',
            url='https://central-bank.example/rates',text=text,checked_at=AT,
            hash=hashlib.sha256(text.encode()).hexdigest())
        j['target_policy_sources']=[source]
        r=j['target_policy_review'];r['source_hashes']['fx1']=source['hash']
        r['observations']['fx']=dict(value=1.3363,evidence=[dict(source_id='fx1',quote=text)])
        pay=dict(minimum=70000,maximum=80000,currency='GBP',period='annual',basis='base')
        self.observation('compensation',pay);self.held()
        pay['maximum']=85000
        result=require_target_policy(self.state,'a',now=NOW)
        self.assertAlmostEqual(result['annual_usd_maximum'],113585.5)
        source['kind']='employer_policy';self.held()

    def test_reserve_and_require_reservation_recheck_target_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=LocalStore(str(Path(tmp)/'state.sqlite'))
            revision,_=store.read();store.save(revision,self.state)
            check=dict(job_id='a',source='linkedin_visible_ui',application_status='not_applied',
                       checked_at=time.time(),url='https://www.linkedin.com/jobs/view/123')
            reserve(store,'a','parent',duplicate_check=check)
            state=store.read()[1];require_reservation(state,'a')
            state['jobs']['a']['target_policy_review']['observations']['abroad_applicants']['value']='unknown'
            with self.assertRaises(Held):require_reservation(state,'a')
            state['paused']=True
            revision,_=store.read();store.save(revision,state)
            with self.assertRaises(Held):reserve(store,'a','parent',duplicate_check=check)

    def test_historical_outcome_recording_remains_available_while_paused(self):
        self.state['paused']=True
        self.state['jobs']['a'].pop('target_policy_review')
        with tempfile.TemporaryDirectory() as tmp:
            store=LocalStore(str(Path(tmp)/'state.sqlite'))
            revision,_=store.read();store.save(revision,self.state)
            Pipeline(store).outcome('a','rejected',{'source':'exact historical receipt'},AT,'receipt-id')
            state=store.read()[1]
            self.assertTrue(state['paused'])
            self.assertEqual(state['jobs']['a']['funnel_events'][-1]['stage'],'rejected')

    def test_formal_guard_cannot_reuse_an_old_policy_screen(self):
        from workflow.safeguards import require_screen
        from workflow.qualification import VERSION as SCREEN_VERSION
        self.state['jobs']['a']['screen']={'policy_version':SCREEN_VERSION}
        with self.assertRaisesRegex(Held,'selection policy changed'):
            require_screen(self.state,self.state['jobs']['a'])
        self.observation('abroad_applicants','unknown')
        with self.assertRaisesRegex(Held,'applicants-from-abroad'):
            require_screen(self.state,self.state['jobs']['a'])

    def test_formal_solutions_senior_screen_keeps_mandatory_engineering_gate(self):
        from workflow.tests.fixtures import candidate, extraction
        from workflow.capture_review import review_body
        from workflow.qualification import screen, VERSION as SCREEN_VERSION
        with tempfile.TemporaryDirectory() as tmp:
            cand=candidate(Path(tmp),years=2,pdf_hash=CV)
            j=self.state['jobs']['a']
            facts=extraction(j,dict(role_family='solutions_engineer',seniority='senior',minimum_years=2))
            j['capture_attestation']=dict(snapshot_hash=j['snapshots'][-1]['hash'],
                body_hash=sha(review_body(facts)),passed=True,policy_version=SCREEN_VERSION)
            self.assertEqual(screen(j,cand,facts,selection_policy=policy())['decision'],'eligible')
            self.assertNotEqual(screen(j,cand,facts)['decision'],'eligible')
            facts['requirements']=[dict(id='python',mandatory=True,text=j['snapshots'][-1]['text'],
                fact_key='skill.Python.years',operator='at_least',value=3)]
            j['capture_attestation']['body_hash']=sha(review_body(facts))
            result=screen(j,cand,facts,selection_policy=policy())
            self.assertEqual(result['decision'],'excluded')
            self.assertIn('requirement:python',result['failures'])


if __name__=='__main__':unittest.main()
