"""Unresolved narrow facts stay recorded and cannot leak into related packages."""
import copy,unittest
from workflow.engine import Held
from workflow.dependencies import require_resolved_package,unresolved_for_package
from workflow.tests import test_pilot

SCOPES={'education_completion': {'subject':'example_university','concepts':['complet','degree','bachelor','master','graduat']},'example_client_delivery_status':{'subject':'example_client','concepts':['production','deliver','deploy','implement','built','shipped','launched']}}
UNRESOLVED=[{'field':'education_completion','reason':'Example University completion unknown'}, {'field':'example_client_delivery_status','reason':'Production delivery unknown'}]
class DependencyScopingTests(unittest.TestCase):
 def setUp(self):self.c={'needs_input':copy.deepcopy(UNRESOLVED),'application_answers':{},'unresolved_scopes':copy.deepcopy(SCOPES)}
 def test_unrelated_package_preserves_unresolved(self):
  before=copy.deepcopy(self.c);require_resolved_package(self.c,{'claims':[]},{'phone':'SYNTHETIC','bachelor_completed':True});self.assertEqual(self.c,before)
 def test_example_university_completion_answer_and_document_hold(self):
  for doc,answers in [({'claims':[]},{'example_university_completed':True}),({'claims':[{'fact_key':'education.example_university.completion','text':'Graduated Example University'}]},{}),({'claims':[],'fact_dependencies':['education_completion']},{})]:
   self.assertRaises(Held,require_resolved_package,self.c,doc,answers)
 def test_example_client_production_answer_and_document_hold(self):
  for doc,answers in [({'claims':[]},{'example_client_production_status':'deployed'}),({'claims':[{'fact_key':'example_client.production','text':'Deployed in production'}]},{}),({'claims':[],'fact_dependencies':['example_client_delivery_status']},{})]:
   self.assertRaises(Held,require_resolved_package,self.c,doc,answers)
 def test_completed_delivery_cannot_hide_behind_generic_claim_key(self):
  for text in ('Built the Example Client workflow', 'Shipped a Example Client integration', 'Launched the Example Client service'):
   self.assertRaises(Held,require_resolved_package,self.c,{'claims':[{'fact_key':'project','text':text}]},{})
 def test_grounded_coursework_and_unrelated_example_client_facts_remain_scoped(self):
  require_resolved_package(self.c,{'claims':[{'fact_key':'education.example_university.coursework','text':'Coursework at Example University'},{'fact_key':'example_client.client','text':'Example Client is a wealth management client'}]}, {})
 def test_explicit_answer_dependencies_hold(self):
  self.c['application_answers']['example']={'fact_dependencies':['example_client_delivery_status']};self.assertRaises(Held,require_resolved_package,self.c,{'claims':[]},{'example':'An example'})
 def test_generic_answer_key_cannot_hide_question_dependency(self):
  self.assertRaises(Held,require_resolved_package,self.c,{'claims':[]},{'degree':True},None,{'degree':{'label':'Did you graduate from Example University?'}})
 def test_unknown_categories_still_global(self):
  self.c['needs_input'].append({'field':'identity_unknown'});self.assertRaises(Held,require_resolved_package,self.c,{'claims':[]},{'phone':'Synthetic'})
 def test_screen_requirement_dependencies_hold(self):
  self.assertRaises(Held,require_resolved_package,self.c,{'claims':[]},{'phone':'Synthetic'},{'requirements':[{'fact_key':'education.example_university.degree'}]})

class PilotScopedGateTests(unittest.TestCase):
 setUp=test_pilot.PilotTests.setUp
 tearDown=test_pilot.PilotTests.tearDown
 bind_grant=test_pilot.PilotTests.bind_grant
 prepared=test_pilot.PilotTests.prepared
 def test_supported_pilot_not_globally_blocked(self):
  self.p.mutate('narrow_unknowns',lambda s:s['candidate'].update(needs_input=copy.deepcopy(UNRESOLVED),unresolved_scopes=copy.deepcopy(SCOPES)));self.bind_grant();aid=self.prepared();self.pilot.open('synthetic',aid)
  self.assertEqual(self.p.store.read()[1]['candidate']['needs_input'],UNRESOLVED)
 def test_related_document_blocks_preparation(self):
  def update(s):
   s['candidate']['needs_input']=copy.deepcopy(UNRESOLVED);s['candidate']['unresolved_scopes']=copy.deepcopy(SCOPES);s['documents'][self.grant['document_id']]['fact_dependencies']=['education_completion']
  self.p.mutate('narrow_unknowns',update);self.bind_grant();self.assertRaises(Held,self.prepared);self.assertEqual(self.b.location,'')
 def test_independent_document_hold_not_removed(self):
  def update(s):
   s['candidate']['needs_input']=copy.deepcopy(UNRESOLVED);s['candidate']['unresolved_scopes']=copy.deepcopy(SCOPES);s['documents'][self.grant['document_id']]['held']=True
  self.p.mutate('narrow_unknowns',update);self.bind_grant();self.assertRaises(Held,self.prepared)
