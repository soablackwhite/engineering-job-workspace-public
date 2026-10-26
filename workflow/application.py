"""Dry-run application adapter: produces only a local, reviewable form plan."""
import json
from pathlib import Path
from workflow.engine import Held

def dry_run(pipeline,jid,token,destination):
    attempt=pipeline.prepare(jid,token) # Durable record BEFORE the local side effect.
    try:
        _,state=pipeline.store.read();j=state['jobs'][jid];candidate=state['candidate']
        plan={'mode':'dry_run','job_id':jid,'attempt_id':attempt,'source':j['sources'][0],
              'document_id':j['documents'][-1],
              'answers':{k:candidate.get(k) for k in ('requires_employer_visa_sponsorship','us_work_authorization')},
              'external_actions':[],'final_submit_enabled':False}
        path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(plan,indent=2)+'\n')
        pipeline.finish(jid,attempt,'dry_run_complete','Local plan saved; no browser/form/upload/submit actions')
        return plan
    except Exception:
        # If recording the failure itself fails, the prepared attempt remains durable and held.
        try:pipeline.finish(jid,attempt,'uncertain','Interrupted during local plan generation; reconcile before further work')
        except Exception:pass
        raise
