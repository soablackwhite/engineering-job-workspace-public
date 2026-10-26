"""Native agent-browser adapter (Hermes' browser backend), with an isolated session."""
import json,os,subprocess
from pathlib import Path
from workflow.engine import Held
class AgentBrowser:
 def __init__(self,session,profile,binary=None):
  self.binary=binary or os.environ.get('JOB_AGENT_BROWSER_BIN')
  if not self.binary or not Path(self.binary).is_file():raise Held('Set JOB_AGENT_BROWSER_BIN to the installed agent-browser executable')
  self.session=session;self.profile=str(Path(profile).resolve())
 def call(self,*args):
  env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','NODE_EXTRA_CA_CERTS') if k in os.environ}
  env['AGENT_BROWSER_NO_AUTO_DIALOG']='1'
  result=subprocess.run([self.binary,'--session',self.session,'--profile',self.profile,'--json',*args],env=env,text=True,capture_output=True,timeout=35)
  if result.returncode:raise Held('Native browser action failed; inspect the session before retrying')
  try:payload=json.loads(result.stdout)
  except ValueError:raise Held('Native browser returned an unrecognized result')
  if payload.get('success') is not True:raise Held('Native browser did not confirm action success')
  return payload.get('data',{})
 def open(self,url):return self.call('open',url)
 def url(self):return self.call('get','url')['url']
 def text(self,selector):return self.call('get','text',selector)['text'].strip()
 def label(self,field):return self.text(field['label_selector'])
 def fill(self,field,value):
  kind=field['kind'];selector=field['selector']
  if kind=='checkbox':return self.call('check' if value is True else 'uncheck',selector)
  return self.call('select' if kind=='select' else 'fill',selector,str(value))
 def value(self,field):
  if field['kind']=='checkbox':return self.call('is','checked',field['selector'])['checked']
  return self.call('get','value',field['selector'])['value']
 def upload(self,selector,path):return self.call('upload',selector,path)
 def uploaded(self,selector):return self.call('get','value',selector)['value'].replace('\\','/')
 def file_hash(self,selector):
  expression='(async()=>{const f=document.querySelector('+json.dumps(selector)+').files[0];if(!f)return null;const b=await f.arrayBuffer();return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",b))).map(x=>x.toString(16).padStart(2,"0")).join("");})()'
  return self.call('eval',expression)['result']
 def required_fields(self):
  expression="Array.from(document.querySelectorAll('input,select,textarea,[aria-required=true]')).filter(e=>e.type!=='hidden' && !e.disabled && (e.required || e.getAttribute('aria-required')==='true')).map(e=>e.id?'#'+CSS.escape(e.id):'UNBOUND_REQUIRED_FIELD')"
  return self.call('eval',expression)['result']
 def click(self,selector):return self.call('click',selector)
 def snapshot(self):return self.call('snapshot')
 def close_fixture(self):return self.call('close')
