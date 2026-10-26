"""Native Hermes tool integration. Grants are operator files, never tool arguments."""
import json,os,sys
from pathlib import Path
_config=Path(__file__).with_name('project-root.json')
_default=json.loads(_config.read_text())['root'] if _config.exists() else str(Path(__file__).resolve().parents[2])
ROOT=Path(os.environ.get('JOB_PIPELINE_ROOT',_default)).resolve()
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def handler(args,**kwargs):
 try:
  from workflow.engine import Pipeline,RemoteStore,Held
  from workflow.pilot import Pilot
  from workflow.browser_driver import AgentBrowser
  from workflow.credentials import TARGET
  pipeline=Pipeline(RemoteStore(TARGET))
  if args['action']=='status':
   _,s=pipeline.store.read()
   return json.dumps({'live_execution_enabled':os.environ.get('JOB_PIPELINE_ENABLE_LIVE')=='supervised','paused':s['paused'],'browser_owner':s['browser'],'jobs':len(s['jobs'])})
  if args['action'] not in ('confirm','reconcile') and os.environ.get('JOB_PIPELINE_ENABLE_LIVE')!='supervised':raise Held('Live application execution is disabled')
  grant_path=os.environ.get('JOB_PILOT_GRANT')
  if not grant_path:raise Held('Operator must supply JOB_PILOT_GRANT for the exact application')
  grant=json.loads(Path(grant_path).read_text())
  if grant['job_id']!=args['jid']:raise Held('Grant targets another job')
  browser=AgentBrowser('job-pipeline-supervised',ROOT/'workflow/runtime/pilot-browser')
  pilot=Pilot(pipeline,browser)
  action=args['action'];jid=args['jid'];aid=args.get('attempt_id')
  if action=='prepare':result=pilot.prepare(jid,args['claim_token'],grant,grant['pdf_path'])
  else:
   _,_,attempt=pilot.current(jid,aid)
   if attempt['grant']!=grant:raise Held('Operator grant changed; reconcile before continuing')
   if action=='fill':result=pilot.fill(jid,aid,args['answer_key'])
   elif action=='submit':
    path=os.environ.get('JOB_PILOT_SUBMIT_APPROVAL')
    if not path:raise Held('Operator must supply approval of the exact form-review hash')
    result=pilot.submit(jid,aid,json.loads(Path(path).read_text()))
   else:result=getattr(pilot,action)(jid,aid)
  return json.dumps({'result':result})
 except Exception as exc:return json.dumps({'held':True,'error':str(exc)})

def guard_raw_tools(tool_name='',**kwargs):
 if tool_name.startswith(('browser_','computer_')) or tool_name in ('terminal','execute_code','write_file','apply_patch','patch'):
  return {'action':'block','message':'This application profile uses job_application for guarded actions. Raw browser/code/file mutation tools cannot bypass its persisted gates.'}
 return None

def register(ctx):
 ctx.register_hook('pre_tool_call',guard_raw_tools)
 ctx.register_tool(name='job_application',toolset='browser',handler=handler,schema={
  'name':'job_application','description':'Persisted supervised job application stages. Live execution defaults off. Never bypass held results with raw browser tools. Grants/submit approval come from operator-configured files, not model arguments.',
  'parameters':{'type':'object','properties':{'action':{'type':'string','enum':['status','prepare','open','fill','upload','review','submit','confirm','reconcile']},'jid':{'type':'string'},'attempt_id':{'type':'string'},'claim_token':{'type':'string'},'answer_key':{'type':'string'}},'required':['action'],'additionalProperties':False}})
