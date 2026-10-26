"""CLI routing tests: providers are opt-in and fixture JSON cannot attest capture."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from workflow import cli
from workflow.engine import Held

class CliWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def invoke(self,args,value):
        f=self.root/'input.json';f.write_text(json.dumps(value));stream=io.StringIO()
        with patch('sys.argv',['workflow.cli',*args,'--input',str(f)]),contextlib.redirect_stdout(stream):cli.main()
        return json.loads(stream.getvalue())
    def test_default_fixture_has_no_provider_or_attestation(self):
        with patch('workflow.cli.store'),patch('workflow.runner.prepare_jobs',return_value={'jobs':[]}) as runner,patch('workflow.model_adapters.capture_adapters') as adapters:
            self.invoke(['prepare-batch'],{'job_ids':['j'],'extractions':{'j':{}},'drafts':{},'reviewer_id':'fake'})
        adapters.assert_not_called()
        self.assertNotIn('reviewer',runner.call_args.kwargs)
        self.assertNotIn('reviewer_id',runner.call_args.kwargs)
    def test_opt_in_has_independent_identity_and_body_free_report(self):
        extract=object();review=SimpleNamespace(config=SimpleNamespace(identity='trusted'))
        raw={'jobs':[{'job_id':'j','screen':{'decision':'needs_input','private_quote':'private'}}],
             'metrics':{'stages':[{'usage':{'input_tokens':12}}]},'external_submissions':0}
        with patch('workflow.cli.store'),patch('workflow.runner.prepare_jobs',return_value=raw) as runner,patch('workflow.model_adapters.capture_adapters',return_value=(extract,review)) as adapters:
            report=self.invoke(['prepare-batch','--adapters','hermes'],{'job_ids':['j'],'drafts':{}})
        adapters.assert_called_once_with(timeout=75)
        self.assertIs(runner.call_args.kwargs['reviewer'],review)
        self.assertEqual(runner.call_args.kwargs['reviewer_id'],'trusted')
        self.assertNotIn('private',json.dumps(report))
        self.assertEqual(report['metrics']['stages'][0]['usage']['input_tokens'],12)
        with self.assertRaises(Held):runner.call_args.args[3]({'id':'j'}, {})
    def test_bootstrap_avoids_store_and_outputs_audit_only(self):
        index={'records':[{'text':'sensitive-note-body'}]};audit={'integrity':True,'record_count':1}
        with patch('workflow.cli.store') as store,patch('workflow.evidence_bootstrap.build',return_value=(index,audit)):
            report=self.invoke(['evidence-bootstrap'],{'config':{'cache':str(self.root/'cache.sqlite')}})
        store.assert_not_called();self.assertTrue(report['integrity'])
        self.assertNotIn('sensitive-note-body',json.dumps(report))
        self.assertEqual(json.loads((self.root/'index.json').read_text()),index)
    def test_bootstrap_remote_refused(self):
        with patch('workflow.cli.store') as store,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            self.invoke(['--remote','https://example.com','evidence-bootstrap'],{})
        store.assert_not_called()
