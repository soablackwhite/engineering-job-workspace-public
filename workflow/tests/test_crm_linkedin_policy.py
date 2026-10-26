import copy
import tempfile
import time
import unittest
from pathlib import Path

from workflow.engine import Held, LocalStore
from workflow.mail_sync import reserve, require_reservation


class CrmLinkedInPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LocalStore(str(Path(self.temp.name) / 'state.sqlite'))
        rev, state = self.store.read()
        state.update(paused=False, submission_controls={
            'audit_status': 'complete', 'gmail_crosscheck_required': False,
            'receipt_check_policy': 'crm_linkedin',
            'gmail_requirement_waiver': {'source': 'direct_user_statement',
                'quote': 'Gmail is not required', 'at': '2026-10-25T14:00:00Z'}})
        state['jobs']['a'] = {'id': 'a', 'company': 'New Employer', 'title': 'Engineer',
            'sources': ['https://www.linkedin.com/jobs/view/123'], 'attempts': []}
        self.store.save(rev, state)
        self.check = {'job_id': 'a', 'source': 'linkedin_visible_ui',
            'application_status': 'not_applied', 'checked_at': time.time(),
            'url': 'https://www.linkedin.com/jobs/view/123/'}

    def test_explicit_waiver_reserves_without_fabricated_mail_evidence(self):
        before = self.store.read()[1].get('gmail_sync')
        reserve(self.store, 'a', 'parent', duplicate_check=self.check)
        state = self.store.read()[1]
        require_reservation(state, 'a')
        self.assertEqual(before, state.get('gmail_sync'))
        self.assertNotIn('gmail_check', state['submission_reservations']['a'])

    def test_missing_explicit_waiver_does_not_disable_mail_gate(self):
        rev, state = self.store.read()
        del state['submission_controls']['gmail_requirement_waiver']
        self.store.save(rev, state)
        with self.assertRaises(Held):
            reserve(self.store, 'a', 'parent', duplicate_check=self.check)

    def test_stale_wrong_applied_and_missing_url_are_rejected(self):
        for change in ({'checked_at': time.time()-601}, {'job_id': 'other'},
                       {'application_status': 'applied'}, {'url': ''}):
            with self.subTest(change=change), self.assertRaises(Held):
                reserve(self.store, 'a', 'parent', duplicate_check={**self.check, **change})

    def test_existing_application_or_hold_cannot_be_waived(self):
        for key, value in [('holds', ['identity']), ('application', {'status': 'Applied'}),
                           ('attempts', [{'id': 'previous'}])]:
            rev, state = self.store.read()
            original = copy.deepcopy(state['jobs']['a'])
            state['jobs']['a'][key] = value
            self.store.save(rev, state)
            with self.subTest(key=key), self.assertRaises(Held):
                reserve(self.store, 'a', 'parent', duplicate_check=self.check)
            rev, state = self.store.read()
            state['jobs']['a'] = original
            self.store.save(rev, state)

    def test_known_employer_application_still_blocks_repost(self):
        rev, state = self.store.read()
        state['jobs']['old'] = {'company': 'New Employer', 'title': 'Other role',
                               'application': {'status': 'Applied'}}
        self.store.save(rev, state)
        with self.assertRaises(Held):
            reserve(self.store, 'a', 'parent', duplicate_check=self.check)

    def test_reservation_must_exist_and_remain_fresh(self):
        with self.assertRaises(Held):
            require_reservation(self.store.read()[1], 'a')
        reserve(self.store, 'a', 'parent', duplicate_check=self.check)
        state = self.store.read()[1]
        state['submission_reservations']['a']['duplicate_check']['checked_at'] -= 601
        with self.assertRaises(Held):
            require_reservation(state, 'a')
