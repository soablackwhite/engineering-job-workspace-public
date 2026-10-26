"""Synthetic checks for the generic public-source boundary."""
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('public_guard', pathlib.Path(__file__).with_name('check_public_tree.py'))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class PublicGuardTests(unittest.TestCase):
    def test_personal_document_and_export_paths_are_rejected(self):
        for path in ('.private/profile.json', 'resume.pdf', 'notes.tex', 'mail.eml',
                     '.openai/hosting.json', 'state.sqlite', 'screenshots/form.png',
                     '.env.production', 'workflow/templates/master-manifest.json',
                     'node_modules', '.venv', 'dist/server/index.js'):
            with self.subTest(path=path):
                self.assertTrue(guard.path_findings(path))

    def test_generic_examples_remain_trackable(self):
        for path in ('.env.example', 'examples/workspace.example.json',
                     'workflow/templates/manifest.example.json', 'public/favicon.svg'):
            self.assertEqual(guard.path_findings(path), [])

    def test_reserved_mailboxes_pass_and_personal_mailboxes_hold(self):
        self.assertEqual(guard.content_findings(b'candidate@example.com'), [])
        mailbox = 'synthetic.person' + '@' + 'mail-provider' + '.com'
        self.assertIn(('non_example_mailbox', 1), guard.content_findings(mailbox.encode()))

    def test_private_keys_and_tokens_are_detected_without_values_in_reports(self):
        key = '-----BEGIN ' + 'PRIVATE KEY-----'
        token = 'ghp_' + 'A' * 40
        findings = guard.content_findings((key + '\n' + token).encode())
        self.assertIn(('private_key', 1), findings)
        self.assertIn(('github_token', 2), findings)
        self.assertNotIn(token, repr(findings))

    def test_machine_paths_are_detected_without_embedding_a_real_home(self):
        path = '/' + 'Users' + '/' + 'synthetic-operator' + '/private/profile.json'
        self.assertIn(('machine_home_path', 1), guard.content_findings(path.encode()))

    def test_embedded_credential_urls_are_detected_but_constructed_fixtures_are_not(self):
        url = 'https://' + 'synthetic-user' + ':' + 'synthetic-value' + '@' + 'example.com'
        self.assertIn(('credential_url', 1), guard.content_findings(url.encode()))
        constructed = "'https://' + 'synthetic-user' + ':' + 'synthetic-value' + '@' + 'example.com'"
        self.assertNotIn(('credential_url', 1), guard.content_findings(constructed.encode()))


if __name__ == '__main__':
    unittest.main()
