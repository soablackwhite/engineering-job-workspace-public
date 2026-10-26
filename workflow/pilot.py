"""Guarded supervised application protocol. Live gates default OFF; fixtures are separate."""
from __future__ import annotations
import copy,hashlib,json,os,time,uuid
from pathlib import Path
from urllib.parse import urlsplit
from workflow.engine import Held,sha
from workflow.safeguards import require_screen, verify_pdf, package, submitted_context
from workflow.qualification import VERSION

PENDING={'open_pending','fill_pending','upload_pending','submit_pending'}
TERMINAL={'confirmed','uncertain','reconciled_held'}

class Pilot:
 def __init__(self,pipeline,browser,*,fixture=False,rehearsal=False):
  self.p=pipeline;self.browser=browser;self.fixture=fixture
  self._mode='presubmit_rehearsal' if rehearsal else ('fixture' if fixture else 'supervised_pilot')
 @property
 def mode(self):return self._mode
 @property
 def rehearsal(self):return self.mode=='presubmit_rehearsal'
 def attempt_mode(self,a):
  if a.get('mode')!=self.mode:raise Held('Attempt mode is immutable; use its original Pilot mode')
 def forbid_submission(self,a=None):
  if self.rehearsal or (a and a.get('mode')=='presubmit_rehearsal'):raise Held('Pre-submit rehearsal cannot submit or confirm a submission')
 def enabled(self,url):
  if self.fixture:
   if urlsplit(url).hostname not in ('127.0.0.1','localhost'):raise Held('Fixtures are restricted to loopback')
  elif os.environ.get('JOB_PIPELINE_ENABLE_LIVE')!=('rehearsal' if self.rehearsal else 'supervised'):raise Held('Live application execution is disabled')
  elif urlsplit(url).scheme!='https':raise Held('Live forms require HTTPS')
  if not self.fixture:
   from workflow.runtime_config import require_operational_context
   try:require_operational_context()
   except (ValueError,OSError):raise Held('Configured private policy, candidate and CV are required before browser work') from None
 def prepare(self,jid,token,grant,pdf):
  self.enabled(grant['url'])
  if grant.get('expires_at',0)<=time.time():raise Held('Unexpired form binding required')
  if not self.rehearsal and grant.get('approved_by')!='user':raise Held('Unexpired user pilot grant required')
  if self.rehearsal and grant.get('allow_submit') is True:raise Held('Rehearsal form binding must exclude submission')
  if grant.get('job_id')!=jid:raise Held('Grant targets another job')
  path=Path(pdf).resolve();pdf_hash=hashlib.sha256(path.read_bytes()).hexdigest()
  if pdf_hash!=grant.get('pdf_sha256'):raise Held('Artifact differs from pilot grant')
  def start(s):
   if s['paused']:raise Held('Queue paused')
   from workflow.mail_sync import require_reservation
   require_reservation(s,jid)
   from workflow.engine import require_application_route
   require_application_route(s,jid,grant['url'])
   j=s['jobs'][jid];self.p.owned(j,token)
   if j['state']!='reviewed' or not j['screen'] or j['screen']['decision']!='eligible':raise Held('Eligibility and review must pass')
   require_screen(s,j)
   doc=s['documents'][j['documents'][-1]];review=doc.get('review') or {}
   if s['candidate'].get('cv_policy')=='single_master_no_tailoring' and doc.get('pdf_sha256')!=s['candidate'].get('master_pdf_sha256'):raise Held('Only the canonical master CV may be submitted')
   if doc.get('held') or review.get('passed') is not True or review.get('semantic_passed') is not True or review.get('reviewer')==doc.get('writer'):raise Held('Independent factual/fit review required')
   if doc.get('pdf_sha256')!=pdf_hash or review.get('pdf_sha256')!=pdf_hash:raise Held('Exact PDF lacks reviewer approval')
   if grant.get('document_id')!=j['documents'][-1] or grant.get('snapshot_hash')!=j['snapshots'][-1]['hash']:raise Held('Grant is stale')
   answers=s['candidate'].get('application_answers',{})
   selected={}
   for key,field in grant.get('fields',{}).items():
    item=answers.get(key,{})
    if item.get('confirmed') is not True or not item.get('source') or 'value' not in item:raise Held('Unconfirmed answer: '+key)
    if field.get('value')!=item['value']:raise Held('Answer differs from confirmed record: '+key)
    if field.get('kind') not in ('text','select','checkbox') or not field.get('selector') or not field.get('label'):raise Held('Invalid field binding')
    selected[key]=item['value']
   from workflow.dependencies import require_resolved_package
   require_resolved_package(s['candidate'],doc,selected,j['screen'].get('facts'),grant['fields'])
   if not selected or grant.get('answers_sha256')!=sha(selected):raise Held('Answer-set hash mismatch')
   contract={k:v for k,v in grant.items() if k not in ('package_sha256','package_version')}
   bound=package(s,j,selected,contract)
   if grant.get('package_version')!=VERSION or grant.get('package_sha256')!=bound['sha256']:raise Held('Approval does not bind the exact current package')
   raw=verify_pdf(s['candidate'],doc,path)
   if j['attempts'] or s['browser']:raise Held('Existing attempt/browser owner requires reconciliation')
   if not grant.get('upload_selector') or (not self.rehearsal and (not grant.get('confirmation') or not grant.get('submit_selector'))):raise Held('Missing form contract')
   aid=str(uuid.uuid4());a={'id':aid,'mode':self.mode,'status':'prepared','created_at':time.time(),
    'grant':copy.deepcopy(grant),'pdf_sha256':pdf_hash,'pdf_path':str(path),'document_id':j['documents'][-1],
    'answers':selected,'answers_sha256':sha(selected),'candidate_hash':bound['payload']['candidate_hash'],'filled':[],
    'events':[],'owner_token':token,'package':bound}
   j.setdefault('dimensions',{}).update(cv_variant=doc.get('variant','unknown'),submission_mode=self.mode)
   submitted_context(s,j,a,raw)
   s['mode']=self.mode
   j['attempts'].append(a);j['state']='attempt_prepared';s['browser']={'job_id':jid,'attempt_id':aid,'token':token}
   return aid
  return self.p.mutate('pilot_prepare',start)
 def current(self,jid,aid):
  _,s=self.p.store.read();j=s['jobs'][jid]
  if not j['attempts'] or j['attempts'][-1]['id']!=aid:raise Held('Attempt missing')
  return s,j,j['attempts'][-1]
 def guard(self,s,j,a):
  self.attempt_mode(a)
  if self.rehearsal:
   from workflow.mail_sync import require_reservation
   require_reservation(s,j['id'])
  from workflow.engine import require_application_route
  require_application_route(s,j['id'],a['grant']['url'])
  self.enabled(a['grant']['url'])
  if s['paused']:raise Held('Queue paused')
  self.p.owned(j,a['owner_token'])
  if not s['browser'] or s['browser']['attempt_id']!=a['id'] or s['browser']['token']!=a['owner_token']:raise Held('Browser lease lost')
  if a['grant']['expires_at']<=time.time():raise Held('Pilot grant expired')
  require_screen(s,j)
  from workflow.dependencies import require_resolved_package
  require_resolved_package(s['candidate'],s['documents'][a['document_id']],a['answers'],j['screen'].get('facts'),a['grant']['fields'])
  bound=package(s,j,a['answers'],{k:v for k,v in a['grant'].items() if k not in ('package_sha256','package_version')})
  if bound!=a.get('package'):raise Held('Application package changed; approval invalid')
  if a['candidate_hash']!=bound['payload']['candidate_hash']:raise Held('Answers changed during application')
  if j['snapshots'][-1]['hash']!=a['grant']['snapshot_hash']:raise Held('Job description changed')
  if j['documents'][-1]!=a['document_id']:raise Held('Reviewed artifact changed')
 def record(self,jid,aid,allowed,status,**data):
  def update(s):
   j=s['jobs'][jid];a=j['attempts'][-1]
   if a['id']!=aid or a['status'] not in allowed:raise Held('Invalid or already-consumed transition')
   self.attempt_mode(a)
   if status in ('submit_pending','submitted','confirmed'):self.forbid_submission(a)
   immutable={'mode','grant','answers','answers_sha256','pdf_sha256','pdf_path','candidate_hash','document_id','owner_token','package','submission_context'}
   if immutable.intersection(data):raise Held('Attempt binding is immutable')
   transitions={'prepared':{'open_pending'},'open_pending':{'opened'},'opened':{'fill_pending'},'fill_pending':{'filled'},'filled':{'fill_pending','upload_pending'},'upload_pending':{'uploaded'},'uploaded':{'form_reviewed'},'form_reviewed':{'submit_pending'},'submit_pending':{'submitted','confirmed'},'submitted':{'confirmed'},'uncertain':{'confirmed'}}
   if status not in transitions.get(a['status'],set()):raise Held('Invalid pilot transition')
   if status=='submit_pending':
    from workflow.mail_sync import require_reservation
    require_reservation(s,jid)
   if status=='confirmed':
    if not s['browser'] or s['browser']['attempt_id']!=aid:raise Held('Reconciliation lease does not match')
   else:self.guard(s,j,a)
   a.update(data);a['status']=status;a['events'].append({'status':status,'at':time.time()});j['state']=status
   if status=='confirmed':
    s['browser']=None
    j.setdefault('funnel_events',[]).append({'id':aid,'stage':'fixture_confirmed' if self.fixture else 'submitted','at':time.time(),'evidence':copy.deepcopy(data.get('confirmation',{}))})
   return copy.deepcopy(a)
  return self.p.mutate('pilot_'+status,update)
 def uncertain(self,jid,aid,reason):
  # Recording an uncertain result must still work after expiry/pause/claim loss.
  def update(s):
   j=s['jobs'][jid];a=j['attempts'][-1]
   if a['id']!=aid or a['status'] in TERMINAL:raise Held('Attempt already terminal')
   a.update(status='uncertain',uncertain_reason=reason);j['state']='uncertain'
  return self.p.mutate('pilot_uncertain',update)
 def effect(self,jid,aid,allowed,pending,finished,fn,**updates):
  a=self.record(jid,aid,allowed,pending)
  try:
   value=fn(a)
   self.record(jid,aid,{pending},finished,**updates)
   return value
  except Exception:
   try:self.uncertain(jid,aid,pending+' did not produce a durable verified outcome')
   except Exception:pass # Pending is already durable; NEVER replay it.
   raise
 def open(self,jid,aid):return self.effect(jid,aid,{'prepared'},'open_pending','opened',lambda a:self.browser.open(a['grant']['url']))
 def at_form(self,a):
  if self.browser.url().rstrip('/')!=a['grant']['url'].rstrip('/'):raise Held('Browser left approved form URL')
  known={f['selector'] for f in a['grant']['fields'].values()} | {a['grant']['upload_selector']}
  if not set(self.browser.required_fields()).issubset(known):raise Held('Unknown required form question; user review required')
 def fill(self,jid,aid,key):
  _,_,a=self.current(jid,aid)
  if key in a['filled'] or key not in a['answers']:raise Held('Field already filled or not approved')
  def action(a):
   self.at_form(a);f=a['grant']['fields'][key]
   if self.browser.label(f)!=f['label']:raise Held('Field label mismatch')
   self.browser.fill(f,a['answers'][key])
   if self.browser.value(f)!=a['answers'][key]:raise Held('Form read-back differs from recorded answer')
  return self.effect(jid,aid,{'opened','filled'},'fill_pending','filled',action,filled=a['filled']+[key])
 def upload(self,jid,aid):
  _,_,a=self.current(jid,aid)
  if set(a['filled'])!=set(a['answers']):raise Held('Required answers incomplete')
  def action(a):
   self.at_form(a);path=Path(a['pdf_path'])
   if hashlib.sha256(path.read_bytes()).hexdigest()!=a['pdf_sha256']:raise Held('PDF changed before upload')
   self.browser.upload(a['grant']['upload_selector'],str(path))
   if Path(self.browser.uploaded(a['grant']['upload_selector'])).name!=path.name or self.browser.file_hash(a['grant']['upload_selector'])!=a['pdf_sha256']:raise Held('Attachment read-back/hash mismatch')
  return self.effect(jid,aid,{'filled'},'upload_pending','uploaded',action)
 def review(self,jid,aid):
  s,j,a=self.current(jid,aid);self.guard(s,j,a)
  if a['status']!='uploaded':raise Held('Upload must complete before form review')
  self.at_form(a)
  for key,f in a['grant']['fields'].items():
   if self.browser.value(f)!=a['answers'][key]:raise Held('A form value changed before review')
  if Path(self.browser.uploaded(a['grant']['upload_selector'])).name!=Path(a['pdf_path']).name or self.browser.file_hash(a['grant']['upload_selector'])!=a['pdf_sha256']:raise Held('Attachment changed')
  evidence={'mode':a['mode'],'package_sha256':a['package']['sha256'],'package_version':a['package']['version'],'job_id':jid,'url':self.browser.url(),'answers':a['answers'],'pdf_sha256':a['pdf_sha256'],'snapshot':self.browser.snapshot()}
  receipt=sha(evidence);self.record(jid,aid,{'uploaded'},'form_reviewed',review_evidence=evidence,review_sha256=receipt)
  return {'review_sha256':receipt,'review':evidence,'submit_enabled':False}
 def submit(self,jid,aid,approval):
  _,_,a=self.current(jid,aid)
  self.forbid_submission(a);self.attempt_mode(a)
  if approval.get('approved_by')!='user' or approval.get('review_sha256')!=a.get('review_sha256') or approval.get('expires_at',0)<=time.time():raise Held('User approval of the exact form review required')
  if a['grant'].get('allow_submit') is not True:raise Held('Pilot grant excludes submission')
  def action(a):
   self.at_form(a)
   for key,f in a['grant']['fields'].items():
    if self.browser.value(f)!=a['answers'][key]:raise Held('Form changed after approval')
   if Path(self.browser.uploaded(a['grant']['upload_selector'])).name!=Path(a['pdf_path']).name or self.browser.file_hash(a['grant']['upload_selector'])!=a['pdf_sha256']:raise Held('Attachment changed after approval')
   # submit_pending was persisted before the ONLY click permitted by this adapter.
   self.browser.click(a['grant']['submit_selector'])
  return self.effect(jid,aid,{'form_reviewed'},'submit_pending','submitted',action,submit_approval=approval)
 def confirm(self,jid,aid):
  s,j,a=self.current(jid,aid)
  self.forbid_submission(a);self.attempt_mode(a)
  if a['status'] not in ('submitted','submit_pending','uncertain'):raise Held('No submission awaiting confirmation')
  contract=a['grant']['confirmation']
  try:
   result={'url':self.browser.url(),'message':self.browser.text(contract['message_selector']),
    'job':self.browser.text(contract['job_selector']),'reference':self.browser.text(contract['reference_selector'])}
   if result['url']!=contract['url'] or result['message']!=contract['message'] or result['job']!=contract['job'] or not result['reference'].strip():raise Held('Confirmation is incomplete or for another job')
   # A pending click after a process crash is only reconciled by positive page evidence.
   self.record(jid,aid,{'submitted','submit_pending','uncertain'},'confirmed',confirmation=result)
   return result
  except Exception:
   if a['status']!='uncertain':self.uncertain(jid,aid,'No matching job-specific confirmation and receipt observed')
   raise

 def reconcile(self,jid,aid):
  """Read-only external reconciliation; never repeat the click."""
  return self.confirm(jid,aid)
