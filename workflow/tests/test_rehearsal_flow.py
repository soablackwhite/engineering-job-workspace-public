"""Synthetic multistep wizard; no browser process or network."""
import json,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
from workflow.engine import Held,sha
from workflow.evidence import EvidenceIndex
from workflow.pilot import Pilot
from workflow.rehearsal_flow import RehearsalFlow
from workflow.tests import test_pilot
from workflow.tests.fixtures import reviewed_screen,extraction

class Wizard(test_pilot.MockBrowser):
 def __init__(self,contracts):super().__init__();self.contracts=contracts;self.page=0;self.actions=[];self.unknown=False
 def observe_step(self):
  c=self.contracts[self.page];fields=[dict(selector=f['selector'],label=f['label'],kind=f['kind'],required=True) for f in c['fields'].values()]
  if self.unknown:fields.append(dict(selector='#salary',label='Mandatory salary',kind='text',required=True))
  return dict(step_id=c['step_id'],fields=fields,upload_selector=c.get('upload_selector'),controls=[c['advance']] if c.get('advance') else [{'selector':'#submit','label':'Submit application'}],snapshot={'page':self.page})
 def click(self,selector):
  self.actions.append(selector)
  if selector not in ('#next1','#next2','#review'):raise AssertionError('Unsafe control')
  self.page+=1
 def review_summary(self):return dict(answers={k:self.values[f['selector']] for c in self.contracts for k,f in c['fields'].items()},pdf_name=Path(self.file).name,pdf_sha256=hashlib.sha256(Path(self.file).read_bytes()).hexdigest())

class FlowTests(unittest.TestCase):
 tearDown=test_pilot.PilotTests.tearDown
 bind_grant=test_pilot.PilotTests.bind_grant
 def setUp(self):
  test_pilot.PilotTests.setUp(self)
  values={'phone':'+15550100','email':'synthetic@example.com','years':'3','auth':False}
  vault=self.path/'vault';facts=vault/'facts.md';facts.write_text(facts.read_text()+'\n\n'+'\n\n'.join('answer.'+k+': '+json.dumps(v) for k,v in values.items()))
  indexer=EvidenceIndex(self.path/'cache.sqlite')
  try:index=indexer.build(vault,['facts.md'])
  finally:indexer.close()
  _,s=self.p.store.read();cand=s['candidate'];cand['evidence_index']=index;cand['application_answers']={k:dict(value=v,confirmed=True,source='fixture vault') for k,v in values.items()};self.p.candidate(cand)
  reviewed_screen(self.p,'synthetic',self.token,extraction(self.p.store.read()[1]['jobs']['synthetic']))
  self.grant['document_id']=self.p.document('synthetic',self.token,{'writer':'fixture-writer','variant':'master','pdf_sha256':self.hash})
  self.p.review('synthetic',self.token,'fixture-reviewer',{'passed':True,'semantic_passed':True,'pdf_sha256':self.hash})
  fields={k:dict(selector='#'+k,label={'phone':'Phone','email':'Email','years':'Years experience','auth':'Authorized to work'}[k],kind='checkbox' if k=='auth' else 'text',value=v) for k,v in values.items()}
  self.grant.update(fields=fields,answers_sha256=sha(values),allow_submit=False);self.grant.pop('approved_by');self.grant.pop('submit_selector');self.grant.pop('confirmation')
  contract={k:v for k,v in self.grant.items() if k not in ('package_sha256','package_version')};bound=self.p.package('synthetic',values,contract);self.grant.update(package_sha256=bound['sha256'],package_version=bound['version'])
  self.contracts=[dict(step_id='contact',fields={k:fields[k] for k in ('phone','email')},advance={'selector':'#next1','label':'Next'},next_step_id='resume'),dict(step_id='resume',fields={},upload_selector='#resume',advance={'selector':'#next2','label':'Next'},next_step_id='questions'),dict(step_id='questions',fields={k:fields[k] for k in ('years','auth')},advance={'selector':'#review','label':'Review'},next_step_id='review'),dict(step_id='review',fields={},final=True)]
  self.b=Wizard(self.contracts);self.pilot=Pilot(self.p,self.b,fixture=True,rehearsal=True);self.aid=self.pilot.prepare('synthetic',self.token,self.grant,self.pdf);self.pilot.open('synthetic',self.aid);self.flow=RehearsalFlow(self.pilot)
 def bind(self,i):return self.flow.bind_step('synthetic',self.aid,self.contracts[i])
 def contact(self):
  self.bind(0)
  for key in ('phone','email'):self.flow.fill('synthetic',self.aid,key)
  self.flow.advance('synthetic',self.aid)
 def complete(self):
  self.contact();self.bind(1);self.flow.upload('synthetic',self.aid);self.flow.advance('synthetic',self.aid);self.bind(2)
  for key in ('years','auth'):self.flow.fill('synthetic',self.aid,key)
  self.flow.advance('synthetic',self.aid);self.bind(3);return self.flow.review('synthetic',self.aid)
 def test_full_multistep_review_zero_submit(self):
  result=self.complete();self.assertFalse(result['submit_enabled']);self.assertEqual(self.b.actions,['#next1','#next2','#review'])
  a=self.pilot.current('synthetic',self.aid)[2];self.assertEqual(a['status'],'form_reviewed');self.assertEqual(len(a['rehearsal_flow']['steps']),4)
  self.assertRaises(Held,self.pilot.submit,'synthetic',self.aid,{});self.assertIsNotNone(self.p.store.read()[1]['browser'])
 def test_unknown_question_stops_before_effect(self):
  self.contact();self.bind(1);self.flow.upload('synthetic',self.aid);self.flow.advance('synthetic',self.aid);self.b.unknown=True
  self.assertRaises(Held,self.bind,2);self.assertNotIn('#years',self.b.values);self.assertEqual(self.b.actions,['#next1','#next2'])
 def test_safe_navigation_catalog_rejects_submit_and_dismiss(self):
  for label in ('Submit','Send','Apply','Dismiss','Next and submit'):
   c=dict(self.contracts[0],advance={'selector':'#submit','label':label});self.assertRaises(Held,self.flow.bind_step,'synthetic',self.aid,c)
  self.assertEqual(self.b.actions,[])
 def test_lost_advance_does_not_replay(self):
  self.bind(0)
  for key in ('phone','email'):self.flow.fill('synthetic',self.aid,key)
  with patch.object(self.b,'click',side_effect=RuntimeError('Lost response')):self.assertRaises(RuntimeError,self.flow.advance,'synthetic',self.aid)
  self.assertRaises(Held,self.flow.advance,'synthetic',self.aid);a=self.pilot.current('synthetic',self.aid)[2];self.assertEqual(a['status'],'uncertain');self.assertIsNotNone(a['rehearsal_flow']['pending'])
 def test_resume_completed_contact_does_not_refill(self):
  self.bind(0);self.flow.fill('synthetic',self.aid,'phone');other=RehearsalFlow(self.pilot);self.assertRaises(Held,other.fill,'synthetic',self.aid,'phone');other.fill('synthetic',self.aid,'email');other.advance('synthetic',self.aid)
 def test_upload_bytes_changed_prevent_advance(self):
  self.contact();self.bind(1);self.flow.upload('synthetic',self.aid);self.pdf.write_bytes(b'changed');self.assertRaises(Held,self.flow.advance,'synthetic',self.aid);self.assertEqual(self.b.actions,['#next1'])
 def test_binding_is_immutable_and_unconfirmed_keys_rejected(self):
  self.bind(0);changed=json.loads(json.dumps(self.contracts[0]));changed['fields']['phone']['selector']='#different'
  self.assertRaises(Held,self.flow.bind_step,'synthetic',self.aid,changed)
  self.assertRaises(Held,self.flow.fill,'synthetic',self.aid,'unconfirmed')
  self.assertEqual(self.b.values,{})
 def test_partial_step_cannot_advance(self):
  self.bind(0);self.flow.fill('synthetic',self.aid,'phone');self.assertRaises(Held,self.flow.advance,'synthetic',self.aid);self.assertEqual(self.b.actions,[])
 def test_changed_final_summary_cannot_review(self):
  with patch.object(self.b,'review_summary',return_value={'answers':{},'pdf_name':self.pdf.name,'pdf_sha256':self.hash}):self.assertRaises(Held,self.complete)
  self.assertNotEqual(self.pilot.current('synthetic',self.aid)[2]['status'],'form_reviewed')

if __name__=='__main__':unittest.main()
