"""Offline integration: real Pipeline/Pilot, disposable notes, synthetic models/forms."""
import base64
import copy
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from workflow.engine import Conflict, Held, LocalStore, Pipeline, sha
from workflow.evidence import EvidenceIndex, current, validate_claims
from workflow.qualification import VERSION
from workflow.runner import prepare_jobs, release_claim
from workflow.safeguards import analytics, migrate
from workflow.tests.fixtures import reviewed_screen, candidate, extraction, approve_capture
from workflow.tests import test_pilot

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.p=Pipeline(LocalStore(self.root/'state.sqlite'))
        self.p.ingest([dict(id='j',title='Applied AI Engineer',company='Synthetic',url='https://example.test/j',
            description='US junior 2 years. Sponsorship available. Python required. PyTorch preferred.',checked_at='2026-10-07')])
        self.p.candidate(candidate(self.root,years=2));self.p.pause(False);self.token=self.p.claim('j','worker')
    def tearDown(self):self.p.store.db.close();self.tmp.cleanup()
    def job(self):return self.p.store.read()[1]['jobs']['j']
    def data(self,**override):return extraction(self.job(),dict(minimum_years=2,**override))
    def screen(self,**override):return reviewed_screen(self.p,'j',self.token,self.data(**override))
    def change(self,key,value):
        file=self.root/'vault/facts.md';paragraphs=file.read_text().split('\n\n')
        paragraphs=[key+': '+json.dumps(value) if p.startswith(key+': ') else p for p in paragraphs]
        file.write_text('\n\n'.join(paragraphs));self.refresh()
    def refresh(self):return self.p.evidence_refresh(str(self.root/'vault'),['facts.md'],str(self.root/'cache.sqlite'))
    def doc(self):return dict(writer='worker',variant='master',pdf_sha256='fixture-pdf')
    def ready(self):
        self.screen();self.p.document('j',self.token,self.doc());self.p.review('j',self.token,'reviewer',dict(passed=True,semantic_passed=True))
    def test_no_sponsorship_allowed_for_authorized_candidate(self):
        self.change('authorization.US',True);self.change('sponsorship_needed.US',False)
        self.assertEqual(self.screen(sponsorship='explicit_no')['decision'],'eligible')
    def test_country_specific_authorization(self):
        self.change('authorization.US',True);self.change('sponsorship_needed.US',False)
        self.assertEqual(self.screen(country='GB',sponsorship='explicit_no')['decision'],'excluded')
    def test_remote_us_is_not_morocco(self):
        self.assertIn('remote_location',self.screen(location=dict(mode='remote',allowed_countries=['US']))['failures'])
    def test_remote_explicitly_allows_morocco(self):
        self.assertEqual(self.screen(location=dict(mode='remote',allowed_countries=['MA']))['decision'],'eligible')
    def test_pay_unknown_or_currency_not_converted_by_guess(self):
        for pay in (None,dict(maximum=200000,currency='GBP',period='annual')):
            self.assertEqual(self.screen(compensation=pay)['decision'],'needs_input')
        self.assertEqual(self.screen(compensation=dict(maximum=80000,currency='USD',period='annual'))['decision'],'excluded')
    def test_stale_conditional_conflicting_or_company_scope_hold(self):
        for key,value in [('checked_at','2020-01-01T00:00:00Z'),('conditional',True),('conflicting',True),('scope','company')]:
            data=self.data();data['observations']['sponsorship'][key]=value;approve_capture(self.job(),data)
            self.assertEqual(reviewed_screen(self.p,'j',self.token,data)['decision'],'needs_input')
    def test_unreviewed_or_tampered_extraction_hold(self):
        data=self.data();data['observations']['minimum_years']['value']=1
        self.assertIn('independent_requirement_capture_review',reviewed_screen(self.p,'j',self.token,data)['questions'])
    def test_malformed_model_output_cannot_draft(self):
        for value in ('not json',{},dict(requirements='Python',observations=[])):
            self.assertEqual(reviewed_screen(self.p,'j',self.token,value)['decision'],'needs_input')
            with self.assertRaises(Held):self.p.document('j',self.token,self.doc())
    def test_malformed_observation_values_and_draft_hold(self):
        for field in ('role_family','country','sponsorship','seniority','minimum_years','location','compensation'):
            data=self.data();data['observations'][field]['value']=[];approve_capture(self.job(),data)
            self.assertEqual(reviewed_screen(self.p,'j',self.token,data)['decision'],'needs_input')
        self.screen()
        for output in (None,[],"invented prose"):
            with self.assertRaises(Held):self.p.document('j',self.token,output)
    def test_receipt_history_projects_outcomes_but_not_missing_dimensions(self):
        state=self.p.store.read()[1];job=state['jobs']['j']
        job['dimensions']={'role_family':'applied_ai'}
        job['reconciliation_events']=['receipt','rejection']
        state['receipt_events']={'receipt':{'event':{'status':'submitted'}},'rejection':{'event':{'status':'rejected'}}}
        report=analytics(state)
        self.assertEqual(report['segments']['role_family']['unknown']['submitted'],1)
        self.assertEqual(report['segments']['role_family']['unknown']['rejected'],1)
    def requirement(self,mandatory,key='skill.PyTorch.years'):
        return dict(id='r',text='PyTorch preferred.',mandatory=mandatory,fact_key=key,operator='at_least',value=1)
    def test_preferred_unmet_is_soft_mandatory_unmet_is_blocker(self):
        for mandatory,decision in [(False,'eligible'),(True,'excluded')]:
            data=self.data();data['requirements']=[self.requirement(mandatory)];approve_capture(self.job(),data)
            result=reviewed_screen(self.p,'j',self.token,data);self.assertEqual(result['decision'],decision)
            self.assertEqual(result['requirement_matrix'][0]['match_strength'],'unmet')
    def test_adjacent_skill_does_not_establish_claim(self):
        data=self.data();data['requirements']=[self.requirement(True,'skill.TensorFlowServing.years')];approve_capture(self.job(),data)
        self.assertEqual(reviewed_screen(self.p,'j',self.token,data)['decision'],'needs_input')
    def test_pm_outside_default_without_deleting_history(self):
        self.p.mutate('synthetic_title',lambda s:s['jobs']['j'].update(title='Product Manager'))
        self.assertIn('role_family',self.screen()['failures']);self.assertIn('j',self.p.store.read()[1]['jobs'])
    def test_cache_hit_edit_delete_and_same_size_change(self):
        self.assertEqual(self.refresh()['cache_hits'],1)
        index=self.p.store.read()[1]['candidate']['evidence_index']
        file=self.root/'vault/facts.md';file.write_text(file.read_text().replace('professional_years: 2','professional_years: 9'))
        self.assertFalse(current(index));self.assertEqual(self.refresh()['cache_misses'],1)
        file.unlink();self.assertFalse(current(index))
    def test_conflicting_sources_never_pick_a_convenient_value(self):
        file=self.root/'vault/facts.md';file.write_text(file.read_text()+'\n\nprofessional_years: 5');self.refresh()
        self.assertEqual(self.screen()['decision'],'needs_input')
    def test_evidence_id_does_not_validate_invented_claim(self):
        index=self.p.store.read()[1]['candidate']['evidence_index'];record=next(r for r in index['records'] if r.get('key')=='skill.Python.years')
        self.assertFalse(validate_claims(index,[dict(text=record['text'],evidence_ids=[record['id']])]))
        for text in ('skill.Python.years: 8','Led Python and PyTorch deployments; saved 50%.','skill.Python.years: 2 for Acme'):
            self.assertTrue(validate_claims(index,[dict(text=text,evidence_ids=[record['id']])]))
    def test_tampered_cached_fact_or_removed_conflict_invalid(self):
        index=self.p.store.read()[1]['candidate']['evidence_index'];index['records'][0]['value']=99
        self.assertFalse(current(index))
        index=self.p.store.read()[1]['candidate']['evidence_index'];index['records'].pop()
        self.assertFalse(current(index))
    def test_unvalidated_documents_blocked_even_with_valid_evidence_id(self):
        self.screen();index=self.p.store.read()[1]['candidate']['evidence_index']
        doc=self.doc();doc['claims']=[dict(text='Invented title',evidence_ids=[index['records'][0]['id']])]
        with self.assertRaises(Held):self.p.document('j',self.token,doc)
    def test_unrelated_prose_edit_preserves_screen_and_package(self):
        self.ready();file=self.root/'vault/facts.md';file.write_text(file.read_text()+'\n\nNew conflicting note.')
        self.p.prepare('j',self.token)
        self.p.package('j',{'name':'SYNTHETIC CANDIDATE'})
    def test_unknown_answer_cannot_get_package(self):
        self.ready()
        with self.assertRaises(Held):self.p.package('j',{'unknown':'plausible answer'})
    def test_migration_is_additive_and_idempotent(self):
        before=dict(schema_version=1,jobs={'old':dict(state='rejected',attempts=[{'status':'uncertain'}],tracking={'notes':'KEEP'})},candidate={'old_fact':7})
        after=migrate(copy.deepcopy(before));self.assertEqual(after['jobs']['old']['state'],'rejected')
        self.assertEqual(after['jobs']['old']['tracking'],before['jobs']['old']['tracking'])
        self.assertEqual(after['jobs']['old']['attempts'],before['jobs']['old']['attempts'])
        self.assertEqual(after['candidate'],before['candidate']);self.assertEqual(migrate(copy.deepcopy(after)),after)
        self.assertEqual(after['jobs']['old']['dimensions']['sponsorship'],'unknown')
    def test_concurrent_claim_only_one_wins(self):
        release_claim(self.p,'j',self.token);barrier=threading.Barrier(2);results=[]
        def worker():
            store=LocalStore(self.root/'state.sqlite');p=Pipeline(store);barrier.wait()
            try:results.append(p.claim('j','contender'))
            except (Held,Conflict):results.append(None)
            finally:store.db.close()
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(r is not None for r in results),1)
    def test_runner_blocks_expensive_work_and_reuses_extraction(self):
        release_claim(self.p,'j',self.token)
        extract=Mock(side_effect=lambda job,ctx:extraction(job,{'minimum_years':10}));draft=Mock()
        first=prepare_jobs(self.p,['j'],extract,draft,reviewer=lambda j,c:extraction(j,{'minimum_years':2}),reviewer_id='second-adapter');second=prepare_jobs(self.p,['j'],extract,draft,reviewer=lambda j,c:extraction(j,{'minimum_years':2}),reviewer_id='second-adapter')
        self.assertEqual(extract.call_count,1);draft.assert_not_called();self.assertEqual(second['metrics']['cache_hits'],1)
        self.assertEqual(first['external_submissions'],0)
    def test_runner_prepares_viable_jobs_and_requires_independent_review(self):
        release_claim(self.p,'j',self.token)
        result=prepare_jobs(self.p,['j'],lambda job,ctx:extraction(job,{'minimum_years':2}),lambda job,ctx:self.doc(),reviewer=lambda j,c:extraction(j,{'minimum_years':2}),reviewer_id='second-adapter')
        self.assertEqual(result['jobs'][0]['state'],'drafted')
        with self.assertRaises(Held):self.p.prepare('j',result['jobs'][0]['claim_token'])
    def test_runner_model_exception_releases_claim_without_retry_loop(self):
        release_claim(self.p,'j',self.token);extract=Mock(side_effect=TimeoutError('mock timeout'))
        result=prepare_jobs(self.p,['j'],extract,Mock());self.assertEqual(extract.call_count,1)
        self.assertIn('held',result['jobs'][0]);self.assertIsNone(self.job()['claim'])
    def test_analytics_segments_and_unknown_cause(self):
        s=self.p.store.read()[1];j=s['jobs']['j'];j['funnel_events']=[dict(stage='submitted'),dict(stage='rejected')]
        j['dimensions']={};result=analytics(s)
        self.assertEqual(result['segments']['sponsorship']['unknown']['rejected'],1)
        self.assertEqual(result['rejection_cause'],'unknown_without_explicit_evidence')
    def test_outcome_idempotence_and_conflicts(self):
        args=dict(jid='j',stage='rejected',evidence='synthetic email-id-1',at='2026-10-07',event_id='one')
        self.p.outcome(**args);self.p.outcome(**args);self.assertEqual(len(self.job()['funnel_events']),1)
        with self.assertRaises(Held):self.p.outcome(**dict(args,stage='offer'))

    def test_repeated_preparation_reuses_exact_draft_and_tracks_usage(self):
        from workflow.runner import StageResult
        release_claim(self.p,'j',self.token)
        extract=Mock(side_effect=lambda j,c:StageResult(extraction(j,{'minimum_years':2}),{'input_tokens':120,'output_tokens':60}))
        draft=Mock(side_effect=lambda j,c:self.doc())
        first=prepare_jobs(self.p,['j'],extract,draft,reviewer=lambda j,c:extraction(j,{'minimum_years':2}),reviewer_id='second-adapter')
        release_claim(self.p,'j',first['jobs'][0]['claim_token'])
        second=prepare_jobs(self.p,['j'],extract,draft,reviewer=lambda j,c:extraction(j,{'minimum_years':2}),reviewer_id='second-adapter')
        self.assertEqual(extract.call_count,1);self.assertEqual(draft.call_count,1)
        self.assertEqual(second['metrics']['document_reuse'],1)
        self.assertEqual(first['metrics']['stages'][0]['usage']['input_tokens'],120)
        self.assertIsNone(first['metrics']['stages'][1]['usage']['input_tokens'])

class PackageTests(unittest.TestCase):
    def setUp(self):self.case=test_pilot.PilotTests();self.case.setUp()
    def tearDown(self):self.case.tearDown()
    def test_package_version_or_contract_change_invalidates_grant(self):
        c=self.case
        c.grant['package_version']='old'
        with self.assertRaises(Held):c.prepared()
        c.bind_grant();c.grant['fields']['name']['label']='Different question'
        with self.assertRaises(Held):c.prepared()
    def test_unknown_required_question_stops_before_submission(self):
        c=self.case;aid=c.prepared();c.pilot.open('synthetic',aid)
        with patch.object(c.b,'required_fields',return_value=['#name','#new-question']):
            with self.assertRaises(Held):c.pilot.fill('synthetic',aid,'name')
        self.assertEqual(c.b.clicks,0)
    def test_exact_document_and_context_archived_before_browser(self):
        c=self.case;aid=c.prepared();s,j,a=c.pilot.current('synthetic',aid)
        self.assertEqual(base64.b64decode(s['artifacts'][c.hash]),c.pdf.read_bytes())
        self.assertEqual(a['submission_context']['dimensions']['role_family'],'applied_ai')
        self.assertEqual(a['answers']['name'],'SYNTHETIC CANDIDATE');self.assertEqual(c.b.location,'')
    def test_in_place_document_edit_invalidates_approval(self):
        c=self.case;aid,approval=c.reviewed()
        c.p.mutate('synthetic_edit',lambda s:s['documents'][c.grant['document_id']].update(content='changed'))
        with self.assertRaises(Held):c.pilot.submit('synthetic',aid,approval)
        self.assertEqual(c.b.clicks,0)
    def test_two_simultaneous_prepare_attempts(self):
        c=self.case;barrier=threading.Barrier(2);results=[]
        from workflow.pilot import Pilot
        def worker():
            store=LocalStore(c.path/'fixture.sqlite');pilot=Pilot(Pipeline(store),test_pilot.MockBrowser(),fixture=True);barrier.wait()
            try:results.append(pilot.prepare('synthetic',c.token,c.grant,c.pdf))
            except (Conflict,Held):results.append(None)
            finally:store.db.close()
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(len(c.p.store.read()[1]['jobs']['synthetic']['attempts']),1)

    def test_preparation_overlaps_guarded_fixture_submission(self):
        from workflow.pilot import Pilot
        c=self.case;release_claim(c.p,'synthetic',c.token)
        c.p.mutate('fixture_reset',lambda s:s['jobs']['synthetic'].update(state='captured',documents=[],screen=None))
        c.p.ingest([dict(id='later',company='Fixture only',title='Applied AI Engineer',url='https://example.test/later',
                       description='US junior three years sponsorship available.',checked_at='2026-10-07')])
        later_started=threading.Event();browser_done=threading.Event()
        def extract(job,context):
            if job['id']=='later':
                later_started.set()
                if not browser_done.wait(3):raise RuntimeError('Browser consumer did not overlap preparation')
            else:
                if not later_started.wait(3):raise RuntimeError('Parallel preparation did not start')
            return extraction(job)
        def draft(job,context):return dict(writer='fixture-writer',variant='master',pdf_sha256=c.hash)
        def ready(item):
            if item['job_id']!='synthetic':return
            c.token=item['claim_token']
            c.p.review('synthetic',c.token,'fixture-reviewer',dict(passed=True,semantic_passed=True,pdf_sha256=c.hash))
            c.grant['document_id']=item['document_id'];c.bind_grant()
            aid,approval=c.reviewed();c.pilot.submit('synthetic',aid,approval);c.pilot.confirm('synthetic',aid)
            browser_done.set()
        result=prepare_jobs(c.p,['synthetic','later'],extract,draft,workers=2,on_ready=ready,reviewer=lambda j,c:extraction(j),reviewer_id='second-adapter')
        self.assertTrue(browser_done.is_set());self.assertEqual(c.b.clicks,1)
        self.assertFalse(any('held' in item for item in result['jobs']))

class LocalFormTests(unittest.TestCase):
    def test_http_form_timeout_reconciles_without_second_post(self):
        """Real local POST; response loss after server accepted the application."""
        from workflow.pilot import Pilot
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'<form><input id="name" required><input id="resume" type="file" required></form>')
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.end_headers();self.wfile.write(b'LOCAL fixture received')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        c=test_pilot.PilotTests();c.setUp();origin='http://127.0.0.1:'+str(server.server_port)
        class Browser(test_pilot.MockBrowser):
            def open(self,url):
                with urlopen(url) as response:self.form=response.read().decode()
                self.location=url
            def required_fields(self):
                import re
                return ['#'+i for i in re.findall(r'id="([^"]+)"[^>]*required',self.form)]
            def click(self,selector):
                self.clicks+=1
                payload=json.dumps(dict(name=self.values['#name'],pdf=base64.b64encode(Path(self.file).read_bytes()).decode())).encode()
                with urlopen(Request(origin+'/complete',data=payload,headers={'Content-Type':'application/json'})) as response:response.read()
                self.location=origin+'/complete'
                raise TimeoutError('Synthetic lost confirmation after accepted POST')
        try:
            c.grant['url']=origin+'/form';c.grant['confirmation']['url']=origin+'/complete';c.bind_grant()
            c.b=Browser();c.pilot=Pilot(c.p,c.b,fixture=True);aid,approval=c.reviewed()
            with self.assertRaises(TimeoutError):c.pilot.submit('synthetic',aid,approval)
            with self.assertRaises(Held):c.pilot.submit('synthetic',aid,approval)
            c.pilot.reconcile('synthetic',aid)
            self.assertEqual(len(received),1);self.assertEqual(received[0]['name'],'SYNTHETIC CANDIDATE')
            self.assertEqual(c.pilot.current('synthetic',aid)[2]['status'],'confirmed')
        finally:server.shutdown();server.server_close();c.tearDown()
