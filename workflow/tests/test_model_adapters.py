import copy
import json
import subprocess
import unittest
from unittest.mock import patch
from workflow.engine import Held
from workflow.model_adapters import AdapterConfig,HermesCaptureAdapter,capture_adapters,strict_json,FIELDS,MODEL

class ModelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.job={'id':'fixture','title':'AI Engineer','company':'Example','snapshots':[{'text':'Requirements: three years. Preferred Python.','source_url':'https://example.test/job'}], 'candidate':'PRIVATE','extraction_cache':'FIRST ANSWER'}
        self.raw={'observations':{k:{'value':None,'quote':'Requirements: three years.','conditional':True,'conflicting':False} for k in FIELDS},'requirements':[{'text':'Preferred Python.','mandatory':False,'fact_key':'skill.Python','operator':'equals','value':True}]}
        self.seen=[]
    def transport(self,packet):
        self.seen.append(copy.deepcopy(packet));return {'text':json.dumps(self.raw),'model':MODEL,'provider':'openai-codex','usage':{'input_tokens':12,'output_tokens':6,'cached_input_tokens':False}}
    def test_source_only_independent_invocations(self):
        a=HermesCaptureAdapter(AdapterConfig('extractor','extract'),self.transport)
        b=HermesCaptureAdapter(AdapterConfig('reviewer','review'),self.transport)
        x=a(self.job,{});y=b(self.job,{})
        self.assertEqual(len(self.seen),2);self.assertEqual([p['role'] for p in self.seen],['extractor','reviewer'])
        self.assertNotIn('PRIVATE',json.dumps(self.seen));self.assertNotIn('FIRST ANSWER',json.dumps(self.seen))
        self.assertEqual(x.usage,{'input_tokens':12,'output_tokens':6});self.assertNotEqual(x.payload['extractor'],y.payload['extractor'])
    def test_reject_candidate_context(self):
        with self.assertRaises(Held):HermesCaptureAdapter(AdapterConfig('reviewer','review'),self.transport)(self.job,{'candidate':'private'})
    def test_strict_json(self):
        for s in ['{"a":1,"a":2}','{"x":NaN}','```json\n{}\n```','{} trailing']:
            with self.assertRaises(Held):strict_json(s)
    def test_schema_source_and_self_attestation_rejected(self):
        for modify in [lambda x:x.update(capture_review={'passed':True}),lambda x:x['requirements'][0].update(mandatory='false'),lambda x:x['observations']['country'].update(quote='invented'),lambda x:x['observations'].pop('country')]:
            with self.subTest(modify=modify):
                raw=copy.deepcopy(self.raw);modify(raw)
                adapter=HermesCaptureAdapter(AdapterConfig('reviewer','review'),lambda p:{'text':json.dumps(raw),'model':MODEL,'provider':'openai-codex'})
                with self.assertRaises(Held):adapter(self.job,{})
    def test_unknown_usage_and_route_hold(self):
        adapter=HermesCaptureAdapter(AdapterConfig('reviewer','review'),lambda p:{'text':json.dumps(self.raw),'model':MODEL,'provider':'openai-codex'})
        self.assertEqual(adapter(self.job).usage,{})
        adapter._transport=lambda p:{'text':json.dumps(self.raw),'model':'other','provider':'openai-codex'}
        with self.assertRaises(Held):adapter(self.job)
    def test_deadline_no_retry(self):
        adapter=HermesCaptureAdapter(AdapterConfig('reviewer','review',hermes_root='/synthetic/runtime',python='/synthetic/python'))
        with patch('workflow.model_adapters.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',80)) as run:
            with self.assertRaises(Held):adapter(self.job)
            self.assertEqual(run.call_count,1)
    def test_factory_separate_pinned_configs(self):
        a,b=capture_adapters(timeout=70)
        self.assertIsNot(a,b);self.assertNotEqual(a.config.role,b.config.role)
        self.assertEqual(a.config.model,MODEL)
        with self.assertRaises(ValueError):AdapterConfig('reviewer','r',model='gpt-6-astra')
    def test_worker_uses_raw_usage_not_shim_defaults(self):
        import io,sys,types
        from contextlib import redirect_stdout
        from workflow.model_adapters import _worker
        aux=types.ModuleType('agent.auxiliary_client')
        aux._parse_codex_final_response=lambda final:None
        def create(**kwargs):
            aux._parse_codex_final_response(types.SimpleNamespace(usage={'input_tokens':17,'input_tokens_details':{'cached_tokens':4}}))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{}'))],usage=types.SimpleNamespace(prompt_tokens=17,completion_tokens=0))
        aux._prepare_aux_request=lambda *a,**k:types.SimpleNamespace(final_model=MODEL,resolved_provider='openai-codex',kwargs={},client=types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))))
        agent=types.ModuleType('agent');agent.__path__=[];agent.auxiliary_client=aux
        packet={'hermes_root':'/nonexistent','role':'reviewer','provider':'openai-codex','model':MODEL,'timeout':75,'source':{'description':'Source'}}
        output=io.StringIO()
        with patch.dict(sys.modules,{'agent':agent,'agent.auxiliary_client':aux}),patch('sys.stdin',io.StringIO(json.dumps(packet))),redirect_stdout(output):_worker()
        self.assertEqual(json.loads(output.getvalue())['usage'],{'input_tokens':17,'cached_input_tokens':4})
    def test_durable_role_isolated_cache_and_usage_attribution(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            root=str(Path(temp)/'cache');seen=[]
            def invoke(packet):seen.append(packet);return self.transport(packet)
            a=HermesCaptureAdapter(AdapterConfig('extractor','e',cache_dir=root),invoke)
            b=HermesCaptureAdapter(AdapterConfig('reviewer','r',cache_dir=root),invoke)
            first=a(self.job);review=b(self.job)
            # A new adapter instance must reuse the on-disk output without calls.
            again=HermesCaptureAdapter(AdapterConfig('extractor','e',cache_dir=root),lambda p:self.fail('paid replay'))(self.job)
            self.assertEqual(len(seen),2);self.assertTrue(again.payload['provider_invocation']['cache_hit'])
            self.assertEqual(again.usage,{'input_tokens':0,'output_tokens':0})
            self.assertEqual(again.payload['provider_invocation']['prior_reported_usage'],{'input_tokens':12,'output_tokens':6})
            self.assertNotIn('cached_input_tokens',again.usage)
            self.assertEqual(first.payload['observations']['country']['checked_at'][:19],again.payload['observations']['country']['checked_at'][:19])
            for file in Path(root).glob('*'):self.assertEqual(file.stat().st_mode&0o777,0o600)
    def test_terminal_failures_and_interrupted_claim_never_retry(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            root=str(Path(temp)/'cache');calls=[]
            def fail(packet):calls.append(packet);raise Held('provider failed')
            a=HermesCaptureAdapter(AdapterConfig('reviewer','r',cache_dir=root),fail)
            self.assertRaises(Held,a,self.job);self.assertRaises(Held,a,self.job);self.assertEqual(len(calls),1)
            cache=next(Path(root).glob('*.json'));body=json.loads(cache.read_text());body['status']='running';cache.write_text(json.dumps(body))
            self.assertRaises(Held,a,self.job);self.assertEqual(len(calls),1)
    def test_source_prompt_and_snapshot_changes_invalidate_only_model_cache(self):
        import tempfile
        from pathlib import Path
        import workflow.model_adapters as module
        with tempfile.TemporaryDirectory() as temp:
            root=str(Path(temp)/'cache');a=HermesCaptureAdapter(AdapterConfig('extractor','e',cache_dir=root),self.transport)
            a(self.job);changed=copy.deepcopy(self.job);changed['candidate']='CHANGED CANDIDATE';changed['extraction_cache']='NEW FIRST ANSWER'
            a(changed);self.assertEqual(len(self.seen),1)
            changed['snapshots'][-1]['hash']='new snapshot';a(changed);self.assertEqual(len(self.seen),2)
            with patch.object(module,'PROMPT',module.PROMPT+' Changed contract.'):a(changed)
            self.assertEqual(len(self.seen),3)
    def test_cached_outputs_do_not_refresh_old_observation_dates(self):
        import tempfile
        from pathlib import Path
        from workflow.qualification import fresh
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as temp:
            root=str(Path(temp)/'cache');a=HermesCaptureAdapter(AdapterConfig('reviewer','r',cache_dir=root),self.transport);a(self.job)
            cache=next(Path(root).glob('*.json'));body=json.loads(cache.read_text());body['completed_at']='2020-01-01T00:00:00+00:00';cache.write_text(json.dumps(body))
            result=a(self.job);date=result.payload['observations']['country']['checked_at']
            self.assertFalse(fresh(date,datetime.now(timezone.utc)));self.assertEqual(len(self.seen),1)
