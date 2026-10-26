"""Narrow JSON CLI bridge used by Hermes profiles. Secrets come only from environment."""
import argparse,json,os
from pathlib import Path
from workflow.engine import Pipeline,LocalStore,RemoteStore,Held

def store(args):
    if args.remote:
        return RemoteStore(args.remote,os.environ.get('JOB_CRM_TOKEN'))
    return LocalStore(args.db)

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='workflow/runtime/app.sqlite');p.add_argument('--remote')
    p.add_argument('action',choices=['preparation-status','prepare-batch','evidence-bootstrap','evidence_refresh','package','analytics','outcome','status','candidate-context','candidate','ingest','pause','resume','claim','screen','document','review','revision','prepare','finish','reconcile','seed'])
    p.add_argument('--input',help='JSON object of method arguments (or raw candidate object for candidate)')
    p.add_argument('--config',type=Path,help='Reviewed evidence bootstrap configuration')
    p.add_argument('--output',type=Path,help='Private bootstrap index output')
    p.add_argument('--adapters',choices=['hermes'],help='Explicitly enable independent provider capture/review')
    a=p.parse_args()
    v=json.loads(Path(a.input).read_text()) if a.input else {}
    if a.action=='evidence-bootstrap':
        if a.remote: p.error('Evidence bootstrap writes local private artifacts only')
        if a.adapters: p.error('Evidence bootstrap does not invoke providers')
        from workflow.evidence_bootstrap import build,private_json
        config=json.loads(a.config.read_text()) if a.config else v.get('config',v)
        index,audit=build(config)
        output=a.output or Path(config['cache']).with_name('index.json')
        private_json(output,index);private_json(output.with_suffix('.audit.json'),audit)
        print(json.dumps(dict(audit,index_path=str(output.resolve())),ensure_ascii=False))
        return
    if a.config or a.output: p.error('--config and --output apply only to evidence-bootstrap')
    if a.adapters and a.action!='prepare-batch': p.error('--adapters applies only to prepare-batch')
    backend=store(a);pipeline=Pipeline(backend)
    if a.action=='prepare-batch':
        from workflow.runner import prepare_jobs
        def draft(job,context):
            document=v.get('drafts',{}).get(job['id'])
            if not isinstance(document,dict): raise Held('Reviewed JSON document missing; no automatic drafting configured')
            return document
        if a.adapters:
            from workflow.model_adapters import capture_adapters
            extract,reviewer=capture_adapters(timeout=v.get('provider_timeout',75))
            result=prepare_jobs(pipeline,v['job_ids'],extract,draft,reviewer=reviewer,
                                reviewer_id=reviewer.config.identity,workers=v.get('workers',2),
                                stage_timeout=v.get('stage_timeout',90))
            # Retain measured stage usage without printing candidate/source bodies.
            result={**{k:value for k,value in result.items() if k!='jobs'},
                    'jobs':[dict(job_id=row['job_id'],**({
                        'held':row['held']} if 'held' in row else {
                        'decision':row.get('screen',{}).get('decision'),
                        'state':row.get('state'),'document_id':row.get('document_id')}))
                        for row in result.get('jobs',[])]}
        else:
            # Explicit JSON fixture mode cannot grant independent attestation.
            result=prepare_jobs(pipeline,v['job_ids'],lambda job,context:v['extractions'][job['id']],
                                draft,workers=v.get('workers',2),stage_timeout=v.get('stage_timeout',90))
    elif a.action=='preparation-status':
        from workflow.runner import preparation_status
        result=preparation_status(pipeline)
    elif a.action=='status':
        rev,s=backend.read();result=dict(revision=rev,state=s)
    elif a.action=='candidate-context':
        rev,s=backend.read();result=dict(revision=rev,context=s['candidate'].get('application_drafting_context'))
    elif a.action=='seed':
        rows=json.loads(Path('lib/jobs.json').read_text());tracking=v.get('tracking',{})
        result=pipeline.ingest([dict(id=r['id'],company=r['Company'],title=r['Role'],url=r['Job / apply link'],tracking=tracking.get(r['id'],{})) for r in rows])
    elif a.action in ('pause','resume'):result=pipeline.pause(a.action=='pause')
    elif a.action=='candidate':result=pipeline.candidate(v)
    else:result=getattr(pipeline,a.action)(**v)
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
