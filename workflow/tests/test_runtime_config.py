"""Unconfigured public checkout cannot touch private service/credential backends."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.engine import RemoteStore
from workflow.credentials import get_token
from workflow.runtime_config import config,service_origin,require_operational_context
from workflow.candidate import build

class PrivateConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.config=self.root/'workspace.json'
        env=patch.dict(os.environ,{'JOB_WORKSPACE_CONFIG':str(self.config)},clear=True)
        env.start();self.addCleanup(env.stop)
    def test_absent_configuration_holds_before_keychain_or_remote(self):
        with patch('workflow.credentials.keychain') as keychain,patch('workflow.engine.build_opener') as remote:
            with self.assertRaises(ValueError):RemoteStore(None,token='synthetic-token')
            with self.assertRaises(ValueError):get_token()
        keychain.assert_not_called();remote.assert_not_called()
    def test_unknown_secret_fields_are_rejected_without_printing_values(self):
        self.config.write_text(json.dumps({'token':'synthetic-sensitive-value'}))
        with self.assertRaises(ValueError) as error:config()
        self.assertNotIn('synthetic-sensitive-value',str(error.exception))
    def test_example_optional_blanks_are_unset_required_fields_still_hold(self):
        example=Path(__file__).resolve().parents[2]/'examples'/'workspace.example.json'
        values=json.loads(example.read_text())
        values['service_origin']='https://crm.example.com'
        values['supplemental_account']=''
        values['hermes_root']='  '
        values['hermes_python']=''
        self.config.write_text(json.dumps(values))
        loaded=config()
        self.assertEqual(service_origin(),'https://crm.example.com')
        for key in ('supplemental_account','hermes_root','hermes_python'):
            self.assertNotIn(key,loaded)
        from workflow.runtime_config import value
        with self.assertRaisesRegex(ValueError,'hermes_root'):value('hermes_root',required=True)
        values['service_origin']='  ';self.config.write_text(json.dumps(values))
        with self.assertRaisesRegex(ValueError,'service_origin'):service_origin()
    def test_nonstring_values_remain_invalid(self):
        self.config.write_text(json.dumps({'receipt_account':False}))
        with self.assertRaisesRegex(ValueError,'must be strings'):config()
    def test_origin_must_not_contain_credentials_path_or_query(self):
        for origin in ['http://crm.example.com','https://'+'name'+':'+'synthetic'+'@'+'crm.example.com','https://crm.example.com/path','https://crm.example.com?key=synthetic']:
            with patch.dict(os.environ,{'JOB_CRM_ORIGIN':origin}):
                with self.assertRaises(ValueError):service_origin()
    def test_partial_configuration_holds_before_real_remote_call(self):
        with patch.dict(os.environ,{'JOB_CRM_ORIGIN':'https://crm.example.com'}):
            store=RemoteStore(None,token='synthetic-token')
            with patch('workflow.engine.build_opener') as remote:
                with self.assertRaises(ValueError):store.call('GET')
            remote.assert_not_called()
    def test_explicit_paths_do_not_infer_candidate_facts(self):
        (self.root/'evidence.md').write_text('# Example work\nSynthetic source only.\n')
        with patch('workflow.candidate.subprocess.check_output',side_effect=['synthetic-revision','']):
            candidate=build(self.root,['evidence.md'])
        self.assertEqual(candidate['skills'],{});self.assertEqual(len(candidate['evidence']),1)
        self.assertEqual(candidate['needs_input'][0]['field'],'reviewed_candidate_facts')
        self.assertNotIn(str(self.root),json.dumps(candidate))
    def test_candidate_source_selection_required_and_cannot_escape(self):
        with self.assertRaises(ValueError):build(self.root)
        with patch('workflow.candidate.subprocess.check_output',side_effect=['synthetic-revision','']):
            with self.assertRaises(ValueError):build(self.root,['../outside.md'])
