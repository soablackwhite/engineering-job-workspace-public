import os
from unittest.mock import patch
import copy,json,unittest
from workflow.engine import RemoteStore,state_changes,Conflict
from workflow.credentials import TARGET

class DeltaTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{"JOB_CRM_ORIGIN":"https://crm.example.com"});env.start();self.addCleanup(env.stop)
    def test_delta_keeps_null_bool_and_array_semantics(self):
        previous={'a':None,'b':{'n':1,'gone':2},'list':[1,2],'same':'x'*2000000}
        current={'a':False,'b':{'n':True,'new':None},'list':[],'same':previous['same']}
        changes=state_changes(previous,current)
        self.assertLess(len(json.dumps(changes)),1000)
        result=copy.deepcopy(previous)
        for change in changes:
            parent=result
            for key in change['path'][:-1]:parent=parent[key]
            key=change['path'][-1]
            if change['op']=='remove':del parent[key]
            else:parent[key]=change['value']
        self.assertEqual(result,current)
        self.assertIs(result['b']['n'],True)

    def test_remote_negotiates_delta_without_mutable_baseline_alias(self):
        from workflow.engine import initial
        store=RemoteStore(TARGET,token='synthetic')
        calls=[]
        def call(method,body=None):
            calls.append((method,body))
            return {'revision':9,'state':initial(),'storage_protocol':'r2-versioned-delta-v1'} if method=='GET' else {'revision':10}
        store.call=call
        revision,state=store.read();state['candidate']['confirmed']=False
        store.save(revision,state)
        self.assertIn('changes',calls[-1][1]);self.assertNotIn('state',calls[-1][1])
        self.assertIn({'path':['candidate','confirmed'],'op':'set','value':False},calls[-1][1]['changes'])
        self.assertIn({'path':['artifacts'],'op':'set','value':{}},calls[-1][1]['changes'])
        with self.assertRaises(Conflict):store.save(9,state)

    def test_legacy_server_still_gets_full_state(self):
        from workflow.engine import initial
        store=RemoteStore(TARGET,token='synthetic');calls=[]
        def call(method,body=None):
            calls.append((method,body));return {'revision':1,'state':initial()} if method=='GET' else {'revision':2}
        store.call=call
        revision,state=store.read();store.save(revision,state)
        self.assertIn('state',calls[-1][1]);self.assertNotIn('changes',calls[-1][1])

if __name__=='__main__':unittest.main()
