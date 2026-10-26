"""Private synchronous RPC to an already-bound CUA tab; never owns a browser.

Create CuaBrowserBridge(queue_path, session=secrets.token_hex(24)). Serve its
requests manually in the existing CUA REPL with processOne(tab, options).
No request is retried. Timeout blocks the session because an effect may already
have happened. A new session requires an operator to reconcile that outcome.
"""
import json,os,re,secrets,threading,time
from pathlib import Path
from workflow.engine import Held

METHODS=frozenset(('open','url','label','fill','value','upload','uploaded','file_hash','required_fields','click','snapshot','observe_step','review_summary'))

class CuaBrowserBridge:
 def __init__(self,queue_path,session,timeout=30):
  if not isinstance(session,str) or not re.fullmatch(r'[a-f0-9]{32,128}',session) or not 0<timeout<=120:raise ValueError('Private session token and finite deadline required')
  self.root=Path(queue_path).absolute();self.session=session;self.timeout=timeout;self.lock=threading.Lock()
  if self.root.is_symlink():raise Held('Queue cannot be a symlink')
  self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
  if self.root.stat().st_uid!=os.getuid() or self.root.stat().st_mode&0o077:raise Held('Queue must be private to its owner')
  for name in ('requests','responses','claims'):
   p=self.root/name
   if p.is_symlink():raise Held('Queue directory cannot be a symlink')
   p.mkdir(mode=0o700,exist_ok=True)
   if p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o077:raise Held('Queue directory must be private')
 def _write(self,path,data):
  tmp=path.with_name('.'+path.name+'.'+secrets.token_hex(8))
  fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  try:
   with os.fdopen(fd,'w') as stream:json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
   os.replace(tmp,path)
  finally:
   if tmp.exists():tmp.unlink()
 def call(self,method,*args):
  if method not in METHODS:raise Held('Browser method outside closed catalog')
  with self.lock:
   blocked=self.root/('blocked-'+self.session+'.json')
   if blocked.exists():raise Held('CUA session outcome unresolved; no replay')
   op=secrets.token_hex(24);deadline=time.time()+self.timeout
   self._write(self.root/'requests'/(op+'.json'),dict(version=1,session=self.session,operation=op,method=method,args=list(args),deadline=deadline))
   response=self.root/'responses'/(op+'.json')
   while time.time()<deadline:
    if response.exists():
     if response.is_symlink() or response.stat().st_mode&0o077:raise Held('Untrusted RPC response permissions')
     try:body=json.loads(response.read_text())
     except (ValueError,OSError):raise Held('Malformed browser RPC response') from None
     if body.get('session')!=self.session or body.get('operation')!=op or body.get('version')!=1 or body.get('deadline')!=deadline:raise Held('Browser RPC binding mismatch')
     if time.time()>=deadline or body.get('completed_at',deadline)>=deadline:break
     if body.get('ok') is not True:raise Held('CUA browser operation held: '+str(body.get('error','unconfirmed result')))
     return body.get('result')
    time.sleep(.03)
   self._write(blocked,dict(operation=op,reason='Deadline expired; inspect actual tab before any new session',at=time.time()))
   raise Held('CUA browser deadline expired; outcome unresolved; never replay')
 def open(self,url):return self.call('open',url)
 def url(self):return self.call('url')
 def label(self,field):return self.call('label',field)
 def fill(self,field,value):return self.call('fill',field,value)
 def value(self,field):return self.call('value',field)
 def upload(self,selector,path):return self.call('upload',selector,str(Path(path).resolve()))
 def uploaded(self,selector):return self.call('uploaded',selector)
 def file_hash(self,selector):return self.call('file_hash',selector)
 def required_fields(self):return self.call('required_fields')
 def click(self,selector):return self.call('click',selector)
 def snapshot(self):return self.call('snapshot')
 def observe_step(self):return self.call('observe_step')
 def review_summary(self):return self.call('review_summary')
