"""Bounded preparation with durable stage status. Never automatically submits.

Callbacks receive copies, must be side-effect-free, and must configure provider
request timeouts. Python cannot kill a hung thread: deadline expiry abandons its
result, retains a durable hold, and consumes one of four process-wide slots until
it actually exits. It cannot keep the interpreter alive or write a late result.
"""
from concurrent.futures import Future, wait, FIRST_COMPLETED
from dataclasses import dataclass
import copy
import json
import threading
import time
import uuid
from workflow.engine import Conflict, Held, sha
from workflow.safeguards import require_screen, validate_document
from workflow.qualification import role_family, triage, VERSION
from workflow.dependencies import screen_keys

_SLOTS = threading.BoundedSemaphore(4)

@dataclass
class StageResult:
    payload: object
    usage: dict  # Provider-reported counts only; absent counts remain unknown.


def compact_context(candidate, keys=()):
    from workflow.evidence import dependency_snapshot
    context = dependency_snapshot(candidate.get('evidence_index', {}), keys)
    if len(json.dumps(context)) > 16000: raise Held('Evidence handoff exceeds 16000 characters')
    return context


def _launch(fn, *args):
    if not _SLOTS.acquire(blocking=False): raise Held('Preparation slots occupied; a prior callback may still be running')
    future = Future()
    def run():
        try:
            future.set_result(fn(*args))
        except BaseException as exc:
            future.set_exception(exc)
        finally: _SLOTS.release()
    threading.Thread(target=run, daemon=True, name='job-preparation').start()
    return future


def metadata_write(pipeline, action, fn):
    # Reevaluate only pure state edits after CAS conflict. Never rerun callbacks.
    for attempt in range(3):
        try: return pipeline.mutate(action, fn)
        except Conflict:
            if attempt == 2: raise


def prepare_jobs(pipeline, job_ids, extract, draft, *, workers=2, owner='preparation',
                 reviewer=None, reviewer_id=None, stage_timeout=90, heartbeat_interval=10, on_ready=None):
    if type(workers) is not int or not 1 <= workers <= 4: raise ValueError('Use 1 to 4 preparation workers')
    if not 0 < stage_timeout <= 600 or not 0 < heartbeat_interval <= 60: raise ValueError('Invalid deadline/heartbeat')
    if reviewer is extract and reviewer is not None: raise ValueError('Reviewer must use a separately configured adapter')
    started=time.monotonic();run_id=str(uuid.uuid4());results=[];pending={};iterator=iter(dict.fromkeys(job_ids))
    _, initial=pipeline.store.read()
    if initial['paused']: return {'held':'Queue paused','jobs':[],'external_submissions':0}
    metrics={'extract_calls':0,'review_calls':0,'draft_calls':0,'cache_hits':0,'document_reuse':0,'stages':[],'early_exclusions':0}

    def record(jid, stage, status, **extra):
        def update(state):
            job=state['jobs'][jid]
            event=dict(run_id=run_id,stage=stage,status=status,at=time.time(),**extra)
            job['preparation']=event
            history=job.setdefault('preparation_history',[]);history.append(event)
            del history[:-12]
        metadata_write(pipeline,'preparation_'+status,update)

    def hold(jid,token,reason,*,uncertain=False):
        record(jid,'preparation','uncertain' if uncertain else 'held',reason=reason)
        results.append({'job_id':jid,'held':reason})
        if not uncertain: release_claim(pipeline,jid,token)

    def submit(jid,token,stage,key,fn,*args):
        # Persist intent before invocation. A crash is visibly unresolved on restart.
        record(jid,stage,'running',deadline_at=time.time()+stage_timeout,owner=owner)
        try:future=_launch(fn,*copy.deepcopy(args))
        except Held as exc:hold(jid,token,str(exc));return
        pending[future]=(jid,token,stage,key,time.monotonic())

    def ready(item):
        # Optional legacy consumer is outside the pure callback pool. Its external
        # effects cannot be inferred from a return value; never report zero for it.
        if on_ready:
            record(item['job_id'],'handoff','running')
            try:on_ready(item)
            except Exception as exc:
                record(item['job_id'],'handoff','uncertain',reason=str(exc))
                item['handoff_error']=str(exc)
            else:record(item['job_id'],'handoff','complete')
        results.append(item)

    def schedule():
        while len(pending)<workers:
            try:jid=next(iterator)
            except StopIteration:return
            _,state=pipeline.store.read();job=state['jobs'][jid]
            if state['paused']:results.append({'job_id':jid,'held':'Queue paused'});return
            if job['attempts']:
                results.append({'job_id':jid,'held':'Existing attempt; reconciliation only'});continue
            if job.get('preparation',{}).get('status') in ('running','uncertain'):
                results.append({'job_id':jid,'held':'Prior preparation unresolved; explicit reconciliation required'});continue
            try:token=pipeline.claim(jid,owner,ttl=max(1800,stage_timeout*4))
            except (Held,Conflict) as exc:results.append({'job_id':jid,'held':str(exc)});continue
            exclusion=triage(job,state['candidate'])
            if exclusion:
                def save_exclusion(s):
                    current_job=s['jobs'][jid];pipeline.owned(current_job,token)
                    if triage(current_job,s['candidate'])!=exclusion:raise Conflict('Triage evidence changed')
                    if current_job['attempts']:raise Held('Existing attempt; reconciliation only')
                    current_job.update(state='excluded',triage=dict(exclusion,policy_version=VERSION,snapshot_hash=current_job['snapshots'][-1]['hash']))
                metadata_write(pipeline,'early_exclusion',save_exclusion)
                metrics['early_exclusions']+=1;record(jid,'triage','complete',decision='excluded')
                results.append({'job_id':jid,'screen':{'decision':'excluded','failures':[exclusion['reason']],'questions':[]},'triage':exclusion})
                release_claim(pipeline,jid,token);continue
            if job.get('state') in ('drafted','reviewed') and job.get('documents'):
                try:
                    require_screen(state,job);doc=state['documents'][job['documents'][-1]]
                    validate_document(state['candidate'],doc)
                    if doc.get('snapshot_hash')!=job['snapshots'][-1]['hash']:raise Held('Description changed')
                except Held:pass
                else:
                    metrics['document_reuse']+=1
                    ready(dict(job_id=jid,document_id=job['documents'][-1],state=job['state'],claim_token=token,reused=True));continue
            key=sha([VERSION,job['title'],job['snapshots'][-1]])
            cache=job.get('extraction_cache',{})
            if cache.get('key')==key:
                metrics['cache_hits']+=1
                # Cached review is checked again by Pipeline.screen, including age.
                try:process(jid,token,'extract',key,cache['value'],0,cached=True)
                except Exception as exc:hold(jid,token,str(exc),uncertain=isinstance(exc,Conflict))
            else:
                metrics['extract_calls']+=1
                submit(jid,token,'extract',key,extract,{k:job[k] for k in ('id','title','company','sources','snapshots')},{})

    def screen_and_draft(jid,token,key,value):
        result=pipeline.screen(jid,token,value)
        if result['decision']!='eligible':
            results.append({'job_id':jid,'screen':result});record(jid,'screen','complete',decision=result['decision'])
            release_claim(pipeline,jid,token);return
        _,state=pipeline.store.read();metrics['draft_calls']+=1
        keys=screen_keys(value)+['document.master.sha256']
        submit(jid,token,'draft',key,draft,state['jobs'][jid],compact_context(state['candidate'],keys))

    def process(jid,token,stage,key,value,elapsed,*,cached=False):
        _,latest=pipeline.store.read()
        latest_job=latest['jobs'][jid]
        if sha([VERSION,latest_job['title'],latest_job['snapshots'][-1]])!=key:raise Held('Job changed during preparation; result discarded')
        usage={'input_tokens':None,'output_tokens':None,'cached_input_tokens':None}
        if isinstance(value,StageResult):
            usage.update({k:v for k,v in value.usage.items() if k in usage and type(v) is int and v>=0});value=value.payload
        if not cached:
            entry=dict(job_id=jid,stage=stage,seconds=elapsed,usage=usage)
            metrics['stages'].append(entry);record(jid,stage,'complete',seconds=elapsed,usage=usage)
        if stage=='extract':
            if not isinstance(value,dict):raise Held('Malformed model extraction')
            if not cached:
                metadata_write(pipeline,'extraction_cache',lambda s:s['jobs'][jid].update(extraction_cache={'key':key,'value':value}))
            _,state=pipeline.store.read();job=state['jobs'][jid]
            from workflow.capture_review import review_body
            attestation=job.get('capture_attestation',{})
            valid_review=(attestation.get('policy_version')==VERSION and attestation.get('passed') is True
                          and attestation.get('body_hash')==sha(review_body(value)) and attestation.get('snapshot_hash')==job['snapshots'][-1]['hash'])
            if valid_review:screen_and_draft(jid,token,key,value)
            elif reviewer is None or not reviewer_id:
                screen_and_draft(jid,token,key,value) # Holds; a fabricated self-review is ignored.
            else:
                metrics['review_calls']+=1
                # Do not give the independent reviewer the first answer, cache or attestation.
                review_job={k:copy.deepcopy(job[k]) for k in ('id','title','company','sources','snapshots')}
                submit(jid,token,'review',key,reviewer,review_job,{})
        elif stage=='review':
            _,state=pipeline.store.read();original=state['jobs'][jid]['extraction_cache']['value']
            pipeline.attest_capture(jid,token,original,value,reviewer_id)
            screen_and_draft(jid,token,key,original)
        else:
            docid=pipeline.document(jid,token,value)
            ready(dict(job_id=jid,document_id=docid,state='drafted',claim_token=token))

    schedule();last_heartbeat=time.monotonic()
    while pending:
        deadline=min(start+stage_timeout for *_,start in pending.values())
        timeout=max(0,min(deadline-time.monotonic(),heartbeat_interval))
        done,_=wait(pending,timeout=timeout,return_when=FIRST_COMPLETED)
        for future in list(pending):
            jid,token,stage,key,start=pending[future];elapsed=time.monotonic()-start
            if future not in done and elapsed<stage_timeout:continue
            pending.pop(future)
            if future not in done:
                hold(jid,token,f'{stage} deadline exceeded; late result discarded, callback may still be running',uncertain=True);continue
            try:process(jid,token,stage,key,future.result(),elapsed)
            except Exception as exc:hold(jid,token,str(exc),uncertain=isinstance(exc,Conflict))
        if time.monotonic()-last_heartbeat>=heartbeat_interval:
            for jid,token,stage,key,start in pending.values():
                record(jid,stage,'running',elapsed_seconds=time.monotonic()-start,deadline_at=time.time()+max(0,stage_timeout-(time.monotonic()-start)))
            last_heartbeat=time.monotonic()
        schedule()
    elapsed=time.monotonic()-started
    metrics.update(seconds=elapsed,completed=len(results),jobs_per_second=len(results)/elapsed if elapsed else 0)
    def finish(state):
        history=state.setdefault('stage_metrics',[]);history.append(dict(run_id=run_id,**metrics));del history[:-20]
    metadata_write(pipeline,'preparation_metrics',finish)
    return {'jobs':results,'metrics':metrics,'external_submissions':None if on_ready else 0,
            'external_effects':'consumer_not_measured' if on_ready else 'none'}


def release_claim(pipeline,jid,token):
    def run(state):
        job=state['jobs'][jid]
        if not job['attempts'] and (job.get('claim') or {}).get('token')==token:job['claim']=None
    metadata_write(pipeline,'release_preparation_claim',run)


def preparation_status(pipeline):
    """Compact read-only status; does not poll models or expose source paragraphs."""
    revision,state=pipeline.store.read();now=time.time()
    jobs=[]
    for job in state['jobs'].values():
        progress=copy.deepcopy(job.get('preparation'))
        if not progress:continue
        if progress.get('status')=='running' and progress.get('deadline_at',float('inf'))<now:progress['overdue']=True
        jobs.append({'job_id':job['id'],'company':job['company'],'title':job['title'],'state':job['state'],'progress':progress})
    last=(state.get('stage_metrics') or [{}])[-1]
    return dict(revision=revision,paused=state['paused'],jobs=jobs,last_run={k:v for k,v in last.items() if k!='stages'})
