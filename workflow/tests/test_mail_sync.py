import copy
import os
from unittest.mock import patch
import json
import tempfile
import time
import unittest
from pathlib import Path
from workflow.engine import Held, LocalStore
from workflow.mail_sync import (apply_bundle, apply_event,
                                plan, reserve, account_state, QUERY_VERSION,
                                PATTERNS, require_reservation, apply_audit)
RECEIPT_ACCOUNT="candidate@example.com"
SUPPLEMENTAL_ACCOUNT="secondary@example.com"

def event(mid='m1', status='submitted', at='2026-09-21T10:00:00+00:00', url='https://www.linkedin.com/jobs/view/123/'):
    return dict(source='gmail_connector', account=RECEIPT_ACCOUNT, message_id=mid,
                thread_id='t1', status=status, company='Example', title='AI Product Manager',
                job_url=url, event_at=at, application_date='2026-09-21',
                excerpt='Application received' if status == 'submitted' else 'Other candidates selected')

class MailSyncTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{"JOB_RECEIPT_ACCOUNT":RECEIPT_ACCOUNT,"JOB_SUPPLEMENTAL_ACCOUNT":SUPPLEMENTAL_ACCOUNT});env.start();self.addCleanup(env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.store = LocalStore(str(Path(self.temp.name) / 'state.sqlite'))
    def tearDown(self): self.temp.cleanup()
    def bundle(self, events=None, **kw):
        events = events if events is not None else [event()]
        result = dict(account=RECEIPT_ACCOUNT, source='gmail_connector', events=events,
                      query=PATTERNS, query_version=QUERY_VERSION,
                      searched_message_ids=[e['message_id'] for e in events], all_pages_fetched=True,
                      next_page_token=None, through='2026-09-22T10:00:00+00:00', kind='initial_targeted_backfill')
        result.update(kw)
        return result
    def test_receipt_replay_and_multiple_notifications_are_one_application(self):
        bundle = self.bundle([event(), event('m2')])
        apply_bundle(self.store, bundle)
        apply_bundle(self.store, bundle)
        _, state = self.store.read()
        self.assertEqual(len(state['jobs']), 1)
        self.assertEqual(len(state['receipt_events']), 2)
        self.assertEqual(len(state['jobs']['linkedin-123']['reconciliation_events']), 2)
        self.assertTrue(state['gmail_sync'][RECEIPT_ACCOUNT]['backfill_complete'])
    def test_rejection_updates_existing_and_old_receipt_cannot_regress_it(self):
        apply_bundle(self.store, self.bundle())
        apply_bundle(self.store, self.bundle([event('m2', 'rejected', '2026-09-22T09:00:00+00:00')]))
        apply_bundle(self.store, self.bundle([event('m3')]))
        _, state = self.store.read()
        self.assertEqual(state['jobs']['linkedin-123']['state'], 'rejected')
        self.assertEqual(len(state['jobs']), 1)
    def test_missing_page_or_message_does_not_advance_checkpoint(self):
        for changes in [{'all_pages_fetched':False}, {'next_page_token':'page2'},
                        {'searched_message_ids':['m1','not_read']}]:
            apply_bundle(self.store, self.bundle(**changes))
            self.assertIsNone(self.store.read()[1]['gmail_sync'][RECEIPT_ACCOUNT]['last_successful_through'])
    def test_wrong_account_never_means_no_duplicates(self):
        result = plan(self.store.read()[1], 'unconfigured@example.com')
        self.assertEqual(result['status'], 'blocked_account_mismatch')
        with self.assertRaises(Held):
            apply_bundle(self.store, self.bundle(account='unconfigured@example.com'))
    def test_incremental_plan_uses_overlap_and_provider_has_no_history_api(self):
        apply_bundle(self.store, self.bundle())
        result = plan(self.store.read()[1], RECEIPT_ACCOUNT, '2026-09-23T10:00:00+00:00')
        self.assertEqual(result['kind'], 'incremental')
        self.assertIn('after:', result['query'])
        self.assertFalse(result['provider_history_api'])
    def test_failed_readback_cannot_advance_checkpoint(self):
        backing = self.store
        class InconsistentReadback:
            count = 0
            def read(self):
                self.count += 1
                revision, state = backing.read()
                if self.count > 1: state['receipt_events'] = {}
                return revision, state
            def save(self, revision, state): backing.save(revision, state)
        with self.assertRaises(Held): apply_bundle(InconsistentReadback(), self.bundle())
        self.assertIsNone(backing.read()[1]['gmail_sync'][RECEIPT_ACCOUNT]['last_successful_through'])
    def test_same_employer_repost_flagged_without_deleting_history(self):
        apply_bundle(self.store, self.bundle())
        second = event('m2', url='https://www.linkedin.com/comm/jobs/view/456/?trk=email')
        second['title'] = 'AI Product Manager LLM RAG'
        apply_bundle(self.store, self.bundle([second]))
        _, state = self.store.read()
        self.assertEqual(len(state['jobs']), 2)
        self.assertEqual(state['jobs']['linkedin-456']['possible_duplicates'], ['linkedin-123'])
        self.assertEqual(len(state['receipt_events']), 2)
    def test_browser_receipt_never_advances_checkpoint(self):
        receipt = event(); receipt.update(source='gmail_browser', evidence_key='thread/job')
        receipt.pop('message_id'); receipt.pop('thread_id')
        mixed = self.bundle([]); mixed['events'] = [receipt]
        with self.assertRaises(Held): apply_bundle(self.store, mixed)
        bundle = self.bundle([], source='gmail_browser'); bundle['events'] = [receipt]
        self.assertFalse(apply_bundle(self.store, bundle)['checkpoint_advanced'])
    def test_shared_reservation_blocks_parallel_alias_and_remains_until_reconciled(self):
        revision, state = self.store.read()
        state['paused'] = False
        state['submission_controls'] = {'audit_status':'complete'}
        account_state(state, RECEIPT_ACCOUNT)['backfill_complete'] = True
        account_state(state, RECEIPT_ACCOUNT)['query_version'] = QUERY_VERSION
        for jid, company in [('a','Example'), ('b','Example Inc')]:
            state['jobs'][jid] = {'company':company,'title':'PM','employer_aliases':['Example'],
                                  'attempts':[], 'possible_duplicates':[]}
        self.store.save(revision, state)
        check = dict(account=RECEIPT_ACCOUNT, job_id='a', query='Example PM',
                     all_pages_fetched=True, clear=True, checked_at=time.time())
        reserve(self.store, 'a', 'worker1', check)
        check['job_id'] = 'b'
        with self.assertRaises(Held): reserve(self.store, 'b', 'worker2', check)

    def test_partial_pages_accumulate_unread_ids_until_explicitly_read(self):
        apply_bundle(self.store, self.bundle(searched_message_ids=['m1', 'unread'],
                     all_pages_fetched=False, next_page_token='page2'))
        result = apply_bundle(self.store, self.bundle([event('m2')]))
        self.assertFalse(result['checkpoint_advanced'])
        state = self.store.read()[1]
        self.assertEqual(plan(state, RECEIPT_ACCOUNT)['remaining_ids'], ['unread'])
        self.assertEqual(state['gmail_sync'][RECEIPT_ACCOUNT]['pending_run']['searched_message_ids'],
                         ['m1', 'm2', 'unread'])
        self.assertTrue(apply_bundle(self.store, self.bundle([event('unread')]))['checkpoint_advanced'])

    def test_partial_scan_cannot_be_discarded_and_errors_need_explicit_resolution(self):
        apply_bundle(self.store, self.bundle(errors=['message read failed']))
        with self.assertRaises(Held):
            apply_bundle(self.store, self.bundle(through='2026-09-23T10:00:00+00:00'))
        self.assertFalse(apply_bundle(self.store, self.bundle())['checkpoint_advanced'])
        self.assertTrue(apply_bundle(self.store, self.bundle(
            resolved_errors=['message read failed']))['checkpoint_advanced'])

    def test_browser_evidence_preserves_connector_pending_run(self):
        apply_bundle(self.store, self.bundle(all_pages_fetched=False, next_page_token='page2'))
        before = copy.deepcopy(self.store.read()[1]['gmail_sync'][RECEIPT_ACCOUNT]['pending_run'])
        receipt = event('browser'); receipt.update(source='gmail_browser', evidence_key='browser')
        receipt.pop('message_id'); receipt.pop('thread_id')
        bundle = self.bundle([], source='gmail_browser'); bundle['events'] = [receipt]
        apply_bundle(self.store, bundle)
        self.assertEqual(self.store.read()[1]['gmail_sync'][RECEIPT_ACCOUNT]['pending_run'], before)

    def test_query_expansion_requires_new_backfill_and_includes_receipt_senders(self):
        apply_bundle(self.store, self.bundle())
        revision, state = self.store.read()
        state['gmail_sync'][RECEIPT_ACCOUNT]['query_version'] = QUERY_VERSION - 1
        self.store.save(revision, state)
        result = plan(state, RECEIPT_ACCOUNT)
        self.assertEqual(result['kind'], 'initial_targeted_backfill')
        self.assertNotIn(' after:', result['query'])
        for sender in ('jobs-noreply@linkedin.com', 'gem.com', 'yello.co'):
            self.assertIn('from:' + sender, result['query'])

    def test_supplemental_receipt_binds_reviewed_job_without_inventing_url(self):
        apply_bundle(self.store, self.bundle())
        receipt = event('supplemental')
        receipt.update(source='gmail_browser', account=SUPPLEMENTAL_ACCOUNT,
                       evidence_key='supplemental1', reviewed_job_id='linkedin-123',
                       identity_basis='Exact employer, title and recorded date reviewed')
        for k in ('job_url', 'message_id', 'thread_id'): receipt.pop(k)
        bundle = self.bundle([], account=SUPPLEMENTAL_ACCOUNT, source='gmail_browser')
        bundle['events'] = [receipt]
        self.assertFalse(apply_bundle(self.store, bundle)['checkpoint_advanced'])
        apply_bundle(self.store, bundle)
        state = self.store.read()[1]
        self.assertEqual(len(state['jobs']), 1)
        self.assertEqual(len(state['receipt_events']), 2)
        self.assertFalse(state['gmail_sync'][SUPPLEMENTAL_ACCOUNT]['backfill_complete'])
        changed = copy.deepcopy(bundle)
        changed['events'][0].update(evidence_key='supplemental2', title='Unrelated job')
        with self.assertRaises(Held): apply_bundle(self.store, changed)
        wrong = self.bundle([]); wrong['events'] = [receipt]
        with self.assertRaises(Held): apply_bundle(self.store, wrong)

    def test_uncertain_historical_employer_holds_future_reservation(self):
        receipt = event(); receipt.pop('job_url')
        apply_bundle(self.store, self.bundle([receipt]))
        revision, state = self.store.read()
        state['paused'] = False
        state['submission_controls'] = {'audit_status':'complete'}
        account_state(state, RECEIPT_ACCOUNT).update(backfill_complete=True, query_version=QUERY_VERSION)
        state['jobs']['new'] = {'company':'Example Inc', 'title':'Different PM role',
                                 'employer_aliases':['Example'], 'attempts':[]}
        self.store.save(revision, state)
        check = dict(account=RECEIPT_ACCOUNT, job_id='new', query='Example',
                     all_pages_fetched=True, clear=True, checked_at=time.time())
        with self.assertRaisesRegex(Held, 'Unresolved historical'):
            reserve(self.store, 'new', 'worker', check)

    def test_prepare_rejects_future_or_wrong_account_reservation(self):
        state = self.store.read()[1]
        state.update(paused=False, submission_controls={'audit_status':'complete', 'gmail_crosscheck_required':True})
        account_state(state, RECEIPT_ACCOUNT).update(backfill_complete=True, query_version=QUERY_VERSION)
        check = dict(account=RECEIPT_ACCOUNT, job_id='a', query='Example',
                     all_pages_fetched=True, clear=True, checked_at=time.time())
        state['submission_reservations'] = {'a': {'job_id':'a', 'gmail_check':check}}
        require_reservation(state, 'a')
        check['checked_at'] = time.time() + 100
        with self.assertRaises(Held): require_reservation(state, 'a')
        check.update(checked_at=time.time(), account='wrong@example.com')
        with self.assertRaises(Held): require_reservation(state, 'a')

    def test_uncertain_message_cannot_disappear_on_later_page(self):
        receipt = event('uncertain'); receipt.pop('job_url')
        apply_bundle(self.store, self.bundle([receipt], searched_message_ids=[],
                     all_pages_fetched=False, next_page_token='page2'))
        self.assertFalse(apply_bundle(self.store, self.bundle())['checkpoint_advanced'])
        pending = self.store.read()[1]['gmail_sync'][RECEIPT_ACCOUNT]['pending_run']
        self.assertEqual(pending['uncertain_ids'], ['uncertain'])

    def test_incremental_run_cannot_skip_initial_backfill(self):
        with self.assertRaises(Held):
            apply_bundle(self.store, self.bundle(kind='incremental'))

    def audit_fixture(self):
        receipt = event(); receipt.update(source='gmail_browser', evidence_key='test-receipt')
        receipt.pop('message_id'); receipt.pop('thread_id')
        bundle = self.bundle([], source='gmail_browser'); bundle['events'] = [receipt]
        folder = Path(self.temp.name)
        (folder/'receipt.json').write_text(json.dumps(bundle))
        report = dict(pending_bundles=['receipt.json'], audit_status='blocked_do_not_resume',
                      limitations=['Connector backfill pending'], duplicate_groups=[],
                      pending_status_corrections=[{'job_id':'linkedin-123','to':'applied'}])
        path = folder/'report.json'; path.write_text(json.dumps(report))
        return path

    def test_whole_audit_readback_and_retry_are_idempotent(self):
        path = self.audit_fixture()
        result = apply_audit(self.store,path)
        self.assertTrue(result['audit_corrections_verified'])
        self.assertFalse(result['checkpoint_advanced'])
        before = self.store.read()[1]
        apply_audit(self.store,path)
        self.assertEqual(self.store.read()[1],before)

    def test_authorized_per_job_check_does_not_falsely_complete_backfill(self):
        rev,state = self.store.read()
        state.update(paused=False,submission_controls={'audit_status':'complete',
            'gmail_crosscheck_required':True,'receipt_check_policy':'per_application',
            'per_application_authorization':'explicit resumed user instruction'})
        state['jobs']['a'] = {'company':'New Employer','title':'PM','attempts':[]}
        self.store.save(rev,state)
        check = dict(account=RECEIPT_ACCOUNT,job_id='a',query='in:anywhere New Employer',
            all_pages_fetched=True,clear=True,checked_at=time.time(),source='gmail_connector',
            profile_verified=True,scope='all_time_employer_role_requisition')
        reserve(self.store,'a','worker',check)
        state = self.store.read()[1]
        require_reservation(state,'a')
        self.assertFalse(state['gmail_sync'][RECEIPT_ACCOUNT]['backfill_complete'])
        self.assertIsNone(state['gmail_sync'][RECEIPT_ACCOUNT]['last_successful_through'])

    def test_per_job_policy_cannot_use_unverified_mailbox_check(self):
        rev,state = self.store.read()
        state.update(paused=False,submission_controls={'audit_status':'complete',
            'receipt_check_policy':'per_application','per_application_authorization':'explicit user'})
        state['jobs']['a'] = {'company':'New Employer','title':'PM','attempts':[]}
        self.store.save(rev,state)
        check = dict(account=RECEIPT_ACCOUNT,job_id='a',query='Employer',
                     all_pages_fetched=True,clear=True,checked_at=time.time())
        with self.assertRaisesRegex(Held,'Verified all-time'):
            reserve(self.store,'a','worker',check)

    def test_paused_wrapup_allows_only_explicit_existing_draft(self):
        rev,state=self.store.read()
        state.update(paused=True,submission_controls={'audit_status':'complete',
            'gmail_crosscheck_required':True,'receipt_check_policy':'per_application',
            'per_application_authorization':'explicit user',
            'draft_wrapup_authorization':{'job_ids':['a'],'source':'User: finish this held draft only','expires_at':time.time()+600}})
        state['jobs']['a']={'company':'New Employer','title':'PM','attempts':[], 'draft_progress':{'status':'needs_input'}}
        state['jobs']['b']={'company':'Other Employer','title':'PM','attempts':[], 'draft_progress':{'status':'needs_input'}}
        self.store.save(rev,state)
        check=dict(account=RECEIPT_ACCOUNT,job_id='a',query='in:anywhere Employer',all_pages_fetched=True,
            clear=True,checked_at=time.time(),source='gmail_connector',profile_verified=True,scope='all_time_employer_role_requisition')
        reserve(self.store,'a','worker',check)
        state=self.store.read()[1];self.assertTrue(state['paused']);require_reservation(state,'a')
        with self.assertRaises(Held):reserve(self.store,'b','worker',dict(check,job_id='b'))
        state['submission_controls']['draft_wrapup_authorization']['expires_at']=0
        with self.assertRaises(Held):require_reservation(state,'a')

    def test_final_readback_must_contain_every_receipt(self):
        backing = self.store
        class LostFinalReceipt:
            count = 0
            def read(self):
                self.count += 1
                rev,state = backing.read()
                if self.count == 5: state['receipt_events'] = {}
                return rev,state
            def save(self,rev,state): backing.save(rev,state)
        with self.assertRaisesRegex(Held,'Final audit evidence'):
            apply_audit(LostFinalReceipt(),self.audit_fixture())

if __name__ == '__main__': unittest.main()
