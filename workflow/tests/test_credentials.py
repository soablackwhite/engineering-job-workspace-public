import os
from unittest.mock import patch
import io
import unittest
from unittest.mock import patch
from workflow.credentials import read_owner_credential, TARGET
from workflow.engine import RemoteStore

class OwnerCredentialTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{"JOB_CRM_ORIGIN":"https://crm.example.com"});env.start();self.addCleanup(env.stop)
    def test_pipe_input_is_consumed_in_memory(self):
        with patch('sys.stdin',io.StringIO('synthetic-test-token\n')):
            self.assertEqual(read_owner_credential(),'synthetic-test-token')
    def test_invalid_input_fails_without_including_secret_in_error(self):
        for token in ['', 'two words', 'x'*8193]:
            with patch('sys.stdin',io.StringIO(token+'\n')):
                with self.assertRaisesRegex(RuntimeError,'^Invalid owner credential input$'):
                    read_owner_credential()
    def test_tty_uses_hidden_prompt(self):
        with patch('sys.stdin.isatty',return_value=True), patch('getpass.getpass',return_value='synthetic-test-token') as prompt:
            self.assertEqual(read_owner_credential(),'synthetic-test-token')
            prompt.assert_called_once_with('CRM owner credential (input hidden): ')
    def test_explicit_token_does_not_read_keychain(self):
        with patch('workflow.credentials.get_token',side_effect=AssertionError('No Keychain read')):
            self.assertEqual(RemoteStore(TARGET,token='synthetic-test-token').token,'synthetic-test-token')
    def test_token_remains_bound_to_existing_private_origin(self):
        with self.assertRaises(ValueError):
            RemoteStore('https://different.example',token='synthetic-test-token')

if __name__=='__main__': unittest.main()
