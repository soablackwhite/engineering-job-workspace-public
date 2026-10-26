"""Incremental intake and idempotent native Kanban graph creation; cards stay blocked."""
import argparse,datetime as dt,json,os
from workflow.engine import LocalStore,RemoteStore,Pipeline,Held,require_application_route
from workflow.discovery import discover,capture
from workflow.hermes_setup import run,ROOT
from workflow.qualification import role_family
from workflow.safeguards import require_screen

def intake(store,limit=10):
    revision,state=store.read()
    if state['paused']:return {'held':'Queue paused','captured':0}
    rows=[];errors=[]
    try:urls=discover(limit)
    except Exception as exc:
        error={'stage':'discovery','error':str(exc)}
        Pipeline(store).mutate('discovery_exception',lambda s:s.setdefault('exceptions',[]).append(error))
        return {'held':'Discovery unavailable','captured':0,'exceptions':[error]}
    for url in urls:
        try:rows.append(capture(url))
        except Exception as exc:errors.append({'url':url,'error':str(exc)})
    p=Pipeline(store)
    from pathlib import Path
    from workflow.engine import sha
    spool=ROOT/'workflow/runtime/intake-spool';spool.mkdir(parents=True,exist_ok=True)
    batch=spool/(sha(rows)+'.json');batch.write_text(json.dumps(rows,ensure_ascii=False)+'\n')
    ids=p.ingest(rows,cursor={'linkedin_last_successful_capture':dt.datetime.now(dt.timezone.utc).isoformat()} if rows else None)
    if errors:p.mutate('capture_exceptions',lambda s:s.setdefault('exceptions',[]).extend(errors))
    _,state=store.read();cards=[];route_holds=[]
    state.setdefault('submission_controls', {})['application_route_policy']='linkedin_easy_apply_only'
    for jid in dict.fromkeys(ids):
        j=state['jobs'][jid]
        if j['attempts']:continue
        try:require_application_route(state,jid)
        except Held as exc:
            route_holds.append({'job_id':jid,'reason':str(exc)});continue
        if role_family(j['title'])=='outside_default':
            route_holds.append({'job_id':jid,'reason':'Outside default engineering role families'});continue
        try:require_screen(state,j);roles=('writer','reviewer','browser')
        except Held:roles=('capture',)
        parent=None
        for role in roles:
            key=jid+':'+j['snapshots'][-1]['hash']+':'+role
            args=['kanban','--board','job-pipeline','create',role+': '+j['company']+' - '+j['title'],
                '--body',f'Read the job-pipeline skill and workflow/profiles/{role}.md; consult only needed README sections. Process app job ID {jid} using workflow.cli. Current snapshot hash {j["snapshots"][-1]["hash"]}. Persist outcomes through the bridge before completing this native task. External submission is disabled. Claims and candidate holds are mandatory. This card awaits supervised dispatch.',
                '--assignee','jobs-'+role,'--idempotency-key',key,'--workspace','dir:'+str(ROOT),
                '--max-runtime','10m','--max-retries','1','--completion-contract','local-only',
                '--initial-status','blocked','--json']
            if parent:args+=['--parent',parent]
            result=json.loads(run(*args));parent=result.get('task',result)['id'];cards.append(parent)
    return {'captured':len(rows),'job_ids':ids,'cards':cards,'exceptions':errors,'route_holds':route_holds}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',default='workflow/runtime/app.sqlite');p.add_argument('--remote');p.add_argument('--limit',type=int,default=10);a=p.parse_args()
    store=RemoteStore(a.remote,os.environ.get('JOB_CRM_TOKEN')) if a.remote else LocalStore(a.db)
    print(json.dumps(intake(store,a.limit),indent=2))
