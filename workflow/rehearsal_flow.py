"""Rehearsal-only wizard; transport exposes typed observations, never raw keys.

Construct with an already-opened rehearsal Pilot. observe_step() returns step_id,
fields (selector,label,kind,required), upload_selector, controls (selector,label),
and snapshot. review_summary() returns complete typed answers, pdf_name and the
verified attachment pdf_sha256 (not a hash inferred from its displayed filename).
"""
import copy,hashlib,time
from pathlib import Path
from workflow.engine import Held,sha

VERSION='presubmit-step-chain-1'

class RehearsalFlow:
 def __init__(self,pilot):
  if not pilot.rehearsal:raise Held('Wizard requires rehearsal mode')
  self.pilot=pilot;self.browser=pilot.browser
 def current(self,jid,aid):
  s,j,a=self.pilot.current(jid,aid);self.pilot.guard(s,j,a)
  if a['status'] not in ('opened','form_reviewed'):raise Held('Wizard must be opened and have no uncertain effect')
  return s,j,a
 def write(self,jid,aid,fn):
  def update(s):
   j=s['jobs'][jid];a=j['attempts'][-1]
   if a['id']!=aid:raise Held('Attempt changed')
   self.pilot.guard(s,j,a)
   flow=a.setdefault('rehearsal_flow',{'version':VERSION,'steps':[],'pending':None})
   if flow['version']!=VERSION:raise Held('Wizard version changed')
   previous=None
   for step in flow['steps']:
    if step['previous_sha256']!=previous or sha(step['binding'])!=step['binding_sha256']:raise Held('Step binding chain changed')
    previous=step['binding_sha256']
   return fn(a,flow)
  return self.pilot.p.mutate('rehearsal_flow',update)
 def observe(self,a):
  if self.browser.url().rstrip('/')!=a['grant']['url'].rstrip('/'):raise Held('Wizard left bound job URL')
  observation=self.browser.observe_step()
  if not observation.get('step_id') or not isinstance(observation.get('fields'),list):raise Held('Incomplete step observation')
  return observation
 def validate(self,a,contract,observation):
  if contract.get('step_id')!=observation['step_id']:raise Held('Observed wizard step differs')
  catalog=a['grant']['fields'];fields=contract.get('fields',{})
  for key,field in fields.items():
   if key not in a['answers'] or field.get('kind')!=catalog[key]['kind'] or field.get('label')!=catalog[key]['label']:raise Held('Unconfirmed question or label')
   if 'value' in field and (type(field['value']) is not type(a['answers'][key]) or field['value']!=a['answers'][key]):raise Held('Unconfirmed typed value')
   matches=[f for f in observation['fields'] if f['selector']==field.get('selector')]
   if len(matches)!=1 or any(matches[0].get(k)!=field.get(k) for k in ('label','kind')):raise Held('Observed field differs from step binding')
  known={f['selector'] for f in fields.values()}
  if contract.get('upload_selector'):
   if contract['upload_selector']!=observation.get('upload_selector'):raise Held('Upload binding differs')
   known.add(contract['upload_selector'])
  if any(f.get('required') and f['selector'] not in known for f in observation['fields']):raise Held('Unknown required question; stop before effect')
  advance=contract.get('advance')
  if advance:
   if advance.get('label') not in ('Next','Review') or not contract.get('next_step_id'):raise Held('Only cataloged Next/Review navigation is permitted')
   if advance not in observation.get('controls',[]):raise Held('Navigation control differs')
  elif not contract.get('final'):raise Held('Intermediate step lacks safe navigation')
  if contract.get('final') and advance:raise Held('Final review cannot navigate')
 def bind_step(self,jid,aid,contract):
  _,_,a=self.current(jid,aid);observation=self.observe(a);self.validate(a,contract,observation)
  binding={'contract':copy.deepcopy(contract),'observation':copy.deepcopy(observation),'observed_sha256':sha(observation),'version':VERSION}
  def bind(a,flow):
   if flow['pending']:raise Held('Pending side effect cannot be replayed')
   steps=flow['steps']
   if steps:
    prior=steps[-1]
    if prior['binding']['contract']['step_id']==contract['step_id']:
     if prior['binding']['contract']!=contract:raise Held('Observed step binding is immutable')
     return prior['binding_sha256']
    if not prior.get('advanced') or prior['binding']['contract'].get('next_step_id')!=contract['step_id']:raise Held('Prior step has not durably advanced')
   elif a['status']!='opened':raise Held('Wizard already reviewed')
   if any(key in step['binding']['contract'].get('fields',{}) for step in steps for key in contract.get('fields',{})):raise Held('Answer already bound to a previous step')
   item={'binding':binding,'binding_sha256':sha(binding),'previous_sha256':steps[-1]['binding_sha256'] if steps else None,'completed':[],'advanced':False}
   steps.append(item);return item['binding_sha256']
  return self.write(jid,aid,bind)
 def step(self,jid,aid):
  _,_,a=self.current(jid,aid);flow=a.get('rehearsal_flow',{})
  if flow.get('pending'):raise Held('Pending side effect cannot be replayed')
  if not flow.get('steps'):raise Held('Observe and bind a step first')
  step=flow['steps'][-1];self.validate(a,step['binding']['contract'],self.observe(a))
  return a,step
 def effect(self,jid,aid,key,fn):
  a,step=self.step(jid,aid)
  def pending(a,flow):
   if flow['pending'] or key in flow['steps'][-1]['completed']:raise Held('Side effect already consumed')
   flow['pending']={'key':key,'binding_sha256':flow['steps'][-1]['binding_sha256'],'at':time.time()}
  self.write(jid,aid,pending)
  try:
   evidence=fn(a,step)
   def complete(a,flow):
    if flow['pending']['key']!=key:raise Held('Pending effect changed')
    flow['steps'][-1]['completed'].append(key);flow['steps'][-1].setdefault('evidence',{})[key]=copy.deepcopy(evidence)
    if key=='advance':flow['steps'][-1]['advanced']=True
    flow['pending']=None
   self.write(jid,aid,complete)
   return evidence
  except Exception:
   try:self.pilot.uncertain(jid,aid,'Wizard side effect outcome uncertain; retain ownership and never replay')
   except Exception:pass
   raise
 def fill(self,jid,aid,key):
  a,step=self.step(jid,aid);field=step['binding']['contract'].get('fields',{}).get(key)
  if not field:raise Held('Field outside bound step catalog')
  def action(a,step):
   self.validate(a,step['binding']['contract'],self.observe(a))
   if self.browser.label(field)!=field['label']:raise Held('Field label changed')
   self.browser.fill(field,a['answers'][key]);value=self.browser.value(field)
   if type(value) is not type(a['answers'][key]) or value!=a['answers'][key]:raise Held('Typed answer readback differs')
   return {'value':value,'selector':field['selector']}
  return self.effect(jid,aid,'answer:'+key,action)
 def verify_upload(self,a,selector):
  path=Path(a['pdf_path'])
  if hashlib.sha256(path.read_bytes()).hexdigest()!=a['pdf_sha256'] or Path(self.browser.uploaded(selector)).name!=path.name or self.browser.file_hash(selector)!=a['pdf_sha256']:raise Held('Exact attachment byte readback differs')
  return {'pdf_sha256':a['pdf_sha256'],'pdf_name':path.name}
 def upload(self,jid,aid):
  a,step=self.step(jid,aid);selector=step['binding']['contract'].get('upload_selector')
  if not selector:raise Held('No upload bound to this step')
  def action(a,step):
   self.validate(a,step['binding']['contract'],self.observe(a))
   if hashlib.sha256(Path(a['pdf_path']).read_bytes()).hexdigest()!=a['pdf_sha256']:raise Held('PDF changed before upload')
   self.browser.upload(selector,a['pdf_path']);return self.verify_upload(a,selector)
  return self.effect(jid,aid,'upload',action)
 def advance(self,jid,aid):
  a,step=self.step(jid,aid);contract=step['binding']['contract'];control=contract.get('advance')
  if not control or control.get('label') not in ('Next','Review'):raise Held('No safe navigation bound')
  expected={'answer:'+key for key in contract.get('fields',{})}|({'upload'} if contract.get('upload_selector') else set())
  if not expected.issubset(set(step['completed'])):raise Held('Step incomplete')
  def action(a,step):
   self.validate(a,contract,self.observe(a))
   for key,field in contract.get('fields',{}).items():
    value=self.browser.value(field)
    if type(value) is not type(a['answers'][key]) or value!=a['answers'][key]:raise Held('Answer changed before advance')
   if contract.get('upload_selector'):self.verify_upload(a,contract['upload_selector'])
   self.browser.click(control['selector'])
   observation=self.observe(a)
   if observation['step_id']!=contract['next_step_id']:raise Held('Navigation did not reach exact next step')
   return {'next_step_id':observation['step_id'],'snapshot':observation}
  return self.effect(jid,aid,'advance',action)
 def review(self,jid,aid):
  a,step=self.step(jid,aid)
  if not step['binding']['contract'].get('final'):raise Held('Not the bound final review step')
  flow=a['rehearsal_flow'];completed={key for item in flow['steps'] for key in item['completed']}
  if not {'answer:'+key for key in a['answers']}.issubset(completed) or 'upload' not in completed:raise Held('Complete answers and verified CV required')
  summary=self.browser.review_summary()
  if sha(summary.get('answers'))!=a['answers_sha256'] or summary.get('pdf_sha256')!=a['pdf_sha256'] or summary.get('pdf_name')!=Path(a['pdf_path']).name:raise Held('Final visible summary differs from complete answer/CV set')
  evidence={'mode':a['mode'],'package_sha256':a['package']['sha256'],'package_version':a['package']['version'],'job_id':jid,'url':self.browser.url(),'answers':a['answers'],'pdf_sha256':a['pdf_sha256'],'snapshot':self.browser.snapshot(),'step_chain':copy.deepcopy(flow),'summary':summary}
  # Existing hosted statuses project the completed wizard proof; nested records
  # retain the exact effect order, including uploads preceding later questions.
  for allowed,status in [('opened','fill_pending'),('fill_pending','filled'),('filled','upload_pending'),('upload_pending','uploaded')]:self.pilot.record(jid,aid,{allowed},status)
  receipt=sha(evidence);self.pilot.record(jid,aid,{'uploaded'},'form_reviewed',review_evidence=evidence,review_sha256=receipt,filled=list(a['answers']))
  return {'review_sha256':receipt,'review':evidence,'submit_enabled':False}
