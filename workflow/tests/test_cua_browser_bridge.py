import json,secrets,tempfile,threading,time,unittest
from pathlib import Path
from workflow.cua_browser_bridge import CuaBrowserBridge
from workflow.engine import Held

class BridgeTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'queue';self.session=secrets.token_hex(24);self.b=CuaBrowserBridge(self.root,self.session,timeout=.2)
 def tearDown(self):self.temp.cleanup()
 def serve(self,result='https://example.test',mutate=None):
  def run():
   for _ in range(100):
    requests=list((self.root/'requests').glob('*.json'))
    if requests:break
    time.sleep(.005)
   req=json.loads(requests[0].read_text());body={**{k:req[k] for k in ('version','session','operation','deadline')},'ok':True,'result':result,'completed_at':time.time()}
   if mutate:mutate(body)
   self.b._write(self.root/'responses'/requests[0].name,body)
  t=threading.Thread(target=run);t.start();return t
 def test_bound_response_and_private_artifacts(self):
  t=self.serve();self.assertEqual(self.b.url(),'https://example.test');t.join()
  for p in self.root.rglob('*.json'):self.assertEqual(p.stat().st_mode&0o777,0o600)
 def test_mismatched_response_holds(self):
  t=self.serve(mutate=lambda x:x.update(session='other'))
  self.assertRaises(Held,self.b.url);t.join()
 def test_late_result_blocks_session_and_no_repeat(self):
  self.assertRaises(Held,self.b.click,'#next');count=len(list((self.root/'requests').glob('*.json')))
  self.assertRaises(Held,self.b.click,'#next');self.assertEqual(len(list((self.root/'requests').glob('*.json'))),count)
 def test_closed_method_catalog(self):
  self.assertRaises(Held,self.b.call,'submit');self.assertEqual(list((self.root/'requests').glob('*')),[])
 def test_weak_session_and_shared_queue_rejected(self):
  self.assertRaises(ValueError,CuaBrowserBridge,self.root,'weak')
  self.root.chmod(0o755);self.assertRaises(Held,CuaBrowserBridge,self.root,self.session)
 def test_symlink_queue_rejected(self):
  link=Path(self.temp.name)/'link';link.symlink_to(self.root);self.assertRaises(Held,CuaBrowserBridge,link,self.session)
