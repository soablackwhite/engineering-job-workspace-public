import copy
import os
from unittest.mock import patch
import unittest
from workflow.engine import Held
from workflow.email_reconcile import normalize_message, classify, identity, reconcile, scan_plan
from workflow.mail_sync import apply_event
RECEIPT_ACCOUNT="candidate@example.com"

def message(mid='m1', subject='Your application to Engineer at Acme',
            text='Your update from Acme', html=None):
    return dict(id=mid,thread_id=mid,date='2026-09-29T10:00:00Z',subject=subject,
                **{'from':'LinkedIn <jobs@example.com>'},text=text,
                html=html or '<a href="https://www.linkedin.com/jobs/view/123?trk=abc">Engineer</a><p>Unfortunately, we will not be moving forward with your application.</p>')

def state():
    return {'jobs':{'linkedin-123':{'company':'Acme','title':'Engineer','state':'applied',
            'sources':['https://www.linkedin.com/jobs/view/123'],'tracking':{'unchanged':True},
            'application':{'application_date':'2026-09-20'}}},'aliases':{},'events':[],
            'paused':True,'submission_reservations':{'held':{'token':'keep'}},
            'gmail_sync':{RECEIPT_ACCOUNT:{'messages':{},'backfill_complete':False,'last_successful_through':None}}}

def manifest(messages):
    return {'account':RECEIPT_ACCOUNT,'query_version':1,'through':'2026-09-30T12:00:00+00:00',
            'queries':[{'query':'in:anywhere before:1790769600','pages':[{'ids':[m['id'] for m in messages],'next_page_token':None}]}],
            'messages':messages}

class Store:
    def __init__(self):self.state=state();self.revision=1;self.saves=0
    def read(self):return self.revision,copy.deepcopy(self.state)
    def save(self,revision,state):
        assert revision==self.revision
        self.state=copy.deepcopy(state);self.revision+=1;self.saves+=1

class EmailReconcileTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{"JOB_RECEIPT_ACCOUNT":RECEIPT_ACCOUNT});env.start();self.addCleanup(env.stop)
    def test_full_mailbox_has_no_sender_subject_or_lower_date_filter(self):
        p=scan_plan(through='2026-09-30T12:00:00+00:00',full_mailbox=True)
        self.assertEqual(p['queries'],[{'name':'entire_mailbox','query':'in:anywhere before:1790769600'}])
        with self.assertRaises(Held):scan_plan(checkpoint='2026-09-29T12:00:00+00:00',full_mailbox=True,since='2026-09-01T00:00:00+01:00')
    def test_campaign_mailbox_range_is_unfiltered_and_timezone_aware(self):
        p=scan_plan(through='2026-10-01T16:00:00+00:00',full_mailbox=True,since='2026-09-01T00:00:00+01:00')
        self.assertEqual(p['queries'][0]['query'],'in:anywhere after:1788217199 before:1790870400')
        with self.assertRaises(Held):scan_plan(full_mailbox=True,since='2026-09-01')
    def test_incremental_mailbox_scan_retains_unfiltered_coverage(self):
        p=scan_plan(checkpoint='2026-09-29T12:00:00+00:00',through='2026-09-30T12:00:00+00:00',full_mailbox=True)
        self.assertEqual(len(p['queries']),1)
        self.assertRegex(p['queries'][0]['query'],r'^in:anywhere after:\d+ before:\d+$')
    def test_unfamiliar_multilingual_decisions_require_review(self):
        for body in ['Malheureusement nous ne pouvons pas poursuivre votre candidature.',
                     'We regret that this position has been filled.',
                     'للأسف لن نتمكن من متابعة طلبك',
                     'Your payment was rejected.']:
            m=message(subject='Update',text=body,html='<p>'+body+'</p>')
            self.assertEqual(classify(normalize_message(m))[0],'review')
    def test_full_mailbox_coverage_keeps_review_pending(self):
        store=Store();m=message(subject='Update',text='Unfortunately we cannot continue.',html='<p>Unfortunately we cannot continue.</p>')
        reconcile(store,manifest([m]))
        run=next(iter(store.state['email_ingestion']['runs'].values()))
        self.assertIn('entire mailbox',run['scope'])
        self.assertEqual(run['pending_review'],1)
        self.assertFalse(store.state['gmail_sync'][RECEIPT_ACCOUNT]['backfill_complete'])
    def test_html_decision_is_read_even_with_plain_part(self):
        m=normalize_message(message());self.assertEqual(classify(m)[0],'rejected')
        self.assertNotIn('trk=',m['text'])
    def test_recommendation_job_cannot_be_target(self):
        m=message(html='<a href="https://www.linkedin.com/jobs/view/123">Engineer</a><p>Unfortunately, we will not be moving forward with your application.</p><p>Top jobs looking for your skills</p><a href="https://www.linkedin.com/jobs/view/999">Engineer</a>')
        normalized=normalize_message(m);self.assertEqual([x['url'] for x in normalized['links']],['https://www.linkedin.com/jobs/view/123'])
        self.assertEqual(identity(normalized,state())[0],'linkedin-123')
    def test_primary_link_conflict_requires_review(self):
        m=normalize_message(message(html='<a href="https://www.linkedin.com/jobs/view/123">Engineer</a><a href="https://www.linkedin.com/jobs/view/999">Engineer</a>'))
        self.assertIsNone(identity(m,state())[0])
    def test_viewed_template_excludes_similar_repost_and_preserves_state(self):
        m=message(subject='Your application was viewed by Acme',text='',html='<a href="https://www.linkedin.com/jobs/view/123">Engineer</a><p>Explore similar jobs recommended for you</p><a href="https://www.linkedin.com/jobs/view/999">Engineer</a>')
        store=Store();reconcile(store,manifest([m]))
        self.assertEqual(store.state['jobs']['linkedin-123']['state'],'applied')
        self.assertEqual(len(store.state['jobs']),1)
        self.assertEqual(store.state['jobs']['linkedin-123']['application']['last_viewed_at'],'2026-09-29T10:00:00+00:00')
    def test_subject_thank_you_can_be_rejection(self):
        m=normalize_message(message(subject='Thank you for applying at Acme',text='We have decided to move forward with other candidates.',html=''))
        self.assertEqual(classify(m)[0],'rejected')
    def test_outgoing_quoted_rejection_ignored(self):
        m=message(subject='Re: Thank you');m['from']=RECEIPT_ACCOUNT
        self.assertEqual(classify(normalize_message(m))[0],'ignored')
    def test_visa_interview_is_not_employment_interview(self):
        m=message(subject='Yatri: Visa Application Confirmation',text='Schedule your interview')
        self.assertEqual(classify(normalize_message(m))[0],'ignored')
    def test_assessment_not_mislabelled_rejection_or_interview(self):
        m=message(subject='Product Manager',text='We invite you to the next stage of our process: Product Manager Assessment.',html='<p>Assessment invitation</p>')
        self.assertEqual(classify(normalize_message(m))[0],'assessment')
    def test_assessment_stage_notice_requires_existing_role_identity(self):
        m=message(subject='Assessment request: Data Engineer',text='Your application for the Data Engineer position with LALAL.AI has reached the assessment stage. Please complete the assessment.',html='<p>Assessment request</p>')
        self.assertEqual(classify(normalize_message(m))[0],'assessment')
        store=Store();result=reconcile(store,manifest([m]))
        self.assertEqual(result['new_events'],0)
        self.assertEqual(result['review'][0]['classification'],'assessment')
        self.assertEqual(store.state['jobs']['linkedin-123']['state'],'applied')
    def test_optional_assessment_precedes_receipt_and_requires_identity(self):
        m=message(subject='Initial Assessment Invitation (Optional)',
                  text='Thank you for applying to Acme, you are invited to optionally take Initial Assessment. You may skip it and we will still process your application.',
                  html='<p>You are invited to optionally take Initial Assessment.</p>')
        normalized=normalize_message(m)
        self.assertEqual(classify(normalized)[0],'assessment')
        self.assertIsNone(identity(normalized,state())[0])
        store=Store();result=reconcile(store,manifest([m]))
        self.assertEqual(result['new_events'],0)
        self.assertEqual(result['review'][0]['classification'],'assessment')
        self.assertEqual(store.state['jobs']['linkedin-123']['state'],'applied')
    def test_agency_client_requires_review(self):
        m=message(subject='Application Received - Engineer @ Client',text='Acme is reviewing your Engineer application.',html='<p>Thank you for applying</p>')
        self.assertIsNone(identity(normalize_message(m),state())[0])
    def test_rejection_replay_preserves_tracking_and_holds(self):
        store=Store();data=manifest([message()]);result=reconcile(store,data)
        self.assertEqual(result['counts']['rejected'],1);saves=store.saves
        reconcile(store,data);self.assertEqual(store.saves,saves)
        self.assertEqual(store.state['jobs']['linkedin-123']['tracking'],{'unchanged':True})
        self.assertEqual(store.state['submission_reservations'],{'held':{'token':'keep'}})
        self.assertFalse(store.state['gmail_sync'][RECEIPT_ACCOUNT]['backfill_complete'])
    def test_partial_pagination_and_unread_ids_block_all_writes(self):
        for change in ['page','unread']:
            store=Store();data=manifest([message()])
            if change=='page':data['queries'][0]['pages'][0]['next_page_token']='next'
            else:data['queries'][0]['pages'][0]['ids'].append('missing')
            with self.assertRaises(Held):reconcile(store,data)
            self.assertEqual(store.saves,0)
    def test_failed_readback_not_success(self):
        class BadStore(Store):
            def read(self):
                r,s=super().read()
                if self.saves:s['jobs']['linkedin-123']['state']='applied'
                return r,s
        with self.assertRaises(Held):reconcile(BadStore(),manifest([message()]))
    def test_mismatched_bound_never_advances_coverage(self):
        s=Store();d=manifest([message()]);d['queries'][0]['query']='in:anywhere before:1'
        with self.assertRaises(Held):reconcile(s,d)
        self.assertEqual(s.saves,0)
    def test_missing_readable_body_requires_review(self):
        m=message();m['html']='';m['text']=''
        self.assertEqual(classify(normalize_message(m))[0],'review')
    def test_delivery_failure_retains_submission_and_prevents_regression(self):
        store=Store();m=message(subject='Problem with your Engineer application',text='There was a problem completing your application.',html='<a href="https://www.linkedin.com/jobs/view/123">Engineer</a>')
        reconcile(store,manifest([m]));job=store.state['jobs']['linkedin-123']
        self.assertEqual(job['state'],'needs_input');self.assertEqual(job['application']['application_date'],'2026-09-20')
        apply_event(store.state,dict(source='gmail_connector',account=RECEIPT_ACCOUNT,message_id='old',thread_id='old',status='submitted',company='Acme',title='Engineer',job_url='https://www.linkedin.com/jobs/view/123',event_at='2026-09-20T10:00:00+00:00',application_date='2026-09-20',excerpt='Application was sent'))
        self.assertEqual(job['state'],'needs_input');self.assertEqual(job['application']['delivery_status'],'failed')
    def test_unknown_identity_is_durable_review_not_new_job(self):
        store=Store();m=message(subject='Update',text='We decided not to move forward.',html='<p>We decided not to move forward.</p>')
        reconcile(store,manifest([m]));self.assertEqual(len(store.state['jobs']),1)
        self.assertIn('m1',store.state['email_ingestion']['review_queue'])
    def test_incremental_overlap_and_fixed_end(self):
        p=scan_plan('2026-09-29T12:00:00+00:00','2026-09-30T12:00:00+00:00')
        self.assertEqual(len(p['queries']),3)
        self.assertTrue(any('from:applytojob.com' in q['query'] and 'subject:assessment' in q['query'] for q in p['queries']))
        for q in p['queries']:self.assertIn('after:',q['query']);self.assertIn('before:',q['query'])

if __name__=='__main__':unittest.main()
