"""Initialize an EMPTY private hosted workflow and upload content-addressed PDFs."""
import argparse,json,os
from pathlib import Path
from urllib.request import Request,urlopen,build_opener
from workflow.engine import RemoteStore,Held,NoRedirect

def bootstrap(url,directory):
    store=RemoteStore(url,os.environ.get('JOB_CRM_TOKEN'));rev,current=store.read()
    if current.get('jobs'):raise Held('Hosted workflow is nonempty; use stage commands, not snapshot replacement')
    directory=Path(directory);state=json.loads((directory/'workflow-state.json').read_text())
    from workflow.runtime_config import value
    from workflow.target_policy import installed,policy_digest
    policy_path=Path(value('selection_policy',required=True))
    policy=json.loads(policy_path.read_text())
    if not installed(policy) or policy.get('digest')!=policy_digest(policy):raise Held('Reviewed private selection policy required')
    if (not state.get('candidate',{}).get('evidence_index') or not state.get('documents')
        or state.get('application_selection_policy')!=policy):raise Held('Private candidate, CV and policy bindings required')
    state['paused']=True
    for doc in state['documents'].values():
        path=directory/('approved-cv.pdf' if doc['variant']=='master' else doc['variant']+'-review-draft.pdf')
        import hashlib
        if hashlib.sha256(path.read_bytes()).hexdigest()!=doc['pdf_sha256']:
            doc['pdf_available']=False
            continue
        doc['pdf_available']=True
        request=Request(url.rstrip('/')+'/api/workflow/document?hash='+doc['pdf_sha256'],data=path.read_bytes(),method='PUT',headers={'Content-Type':'application/pdf','OAI-Sites-Authorization':'Bearer '+store.token})
        with build_opener(NoRedirect()).open(request,timeout=30) as response:json.load(response)
    state['paused']=True;store.save(rev,state)
    return {'job_count':len(state['jobs']),'paused':True,'mode':state['mode']}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('url');p.add_argument('directory');a=p.parse_args();print(json.dumps(bootstrap(a.url,a.directory)))
