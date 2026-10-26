"""Durable job workflow. No method performs external application submission."""
from __future__ import annotations
import copy, hashlib, json, sqlite3, time, uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from workflow.safeguards import migrate, require_screen, validate_document
from workflow.qualification import screen as evidence_screen, VERSION

class Conflict(RuntimeError): pass
class Held(RuntimeError): pass

def require_application_route(state, jid, target_url=None):
    """An observed Easy Apply form, not a LinkedIn URL/search filter alone."""
    if state.get('submission_controls', {}).get('application_route_policy') != 'linkedin_easy_apply_only':
        return
    if target_url is not None and (urlsplit(target_url).hostname or '').lower() not in ('linkedin.com','www.linkedin.com'):
        raise Held('External ATS target outside LinkedIn Easy Apply phase')
    job = state['jobs'][jid]
    route = job.get('application_route', {})
    url = route.get('source_url', '')
    host = (urlsplit(url).hostname or '').lower()
    if (route.get('kind') != 'linkedin_easy_apply' or route.get('verified') is not True
        or route.get('label') != 'Easy Apply' or not route.get('checked_at')
        or host not in ('linkedin.com', 'www.linkedin.com')
        or canonical(url) not in job.get('sources', [])):
        raise Held('Current phase requires verified LinkedIn Easy Apply; external or unknown route held')

def initial():
    return dict(schema_version=2, mode='dry_run', paused=True, jobs={}, aliases={},
                browser=None, candidate={}, documents={}, events=[], cursors={})

def canonical(url):
    p=urlsplit(url)
    if p.scheme != 'https' or not p.hostname:
        raise ValueError('An HTTPS job source URL is required')
    import re
    match=re.search(r'/jobs/view/(?:[^/]*-)?(\d+)',p.path)
    if p.hostname in ('linkedin.com','www.linkedin.com') and match:
        return 'https://www.linkedin.com/jobs/view/'+match.group(1)
    return urlunsplit(('https',p.netloc.lower(),p.path.rstrip('/'),'',''))

def sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class LocalStore:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=20)
        self.db.execute('CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,payload TEXT NOT NULL)')
        self.db.execute('INSERT OR IGNORE INTO state VALUES (1,0,?)',(json.dumps(initial()),));self.db.commit()
    def read(self):
        rev,payload=self.db.execute('SELECT revision,payload FROM state WHERE id=1').fetchone()
        return rev,migrate(json.loads(payload))
    def save(self,revision,state):
        payload=json.dumps(state,ensure_ascii=False)
        if len(payload.encode())>1_500_000: raise Held('State capacity reached; archive snapshots before next import')
        result=self.db.execute('UPDATE state SET revision=revision+1,payload=? WHERE id=1 AND revision=?',(payload,revision))
        self.db.commit()
        if result.rowcount!=1:raise Conflict('State changed; reread before retrying')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):raise Held('API redirect refused; credentials are never forwarded to a login or another origin')

def state_changes(previous, current, path=()):
    """Object-key deltas; arrays are replaced atomically and null stays a value."""
    if isinstance(previous, dict) and isinstance(current, dict):
        changes=[]
        for key in previous.keys()-current.keys():
            changes.append({'path':[*path,key],'op':'remove'})
        for key,value in current.items():
            if key not in previous:
                changes.append({'path':[*path,key],'op':'set','value':value})
            else:
                changes.extend(state_changes(previous[key],value,(*path,key)))
        return changes
    if type(previous) is type(current) and previous==current:return []
    return [{'path':list(path),'op':'set','value':current}]

class RemoteStore:
    def __init__(self,url,token=None):
        from workflow.runtime_config import service_origin
        origin=service_origin()
        if url is not None and url.rstrip('/')!=origin:raise ValueError('Credential is bound to the configured private CRM origin')
        self.url=origin+'/api/workflow';self._token=token
        self._baseline=None;self._revision=None;self._delta=False
    @property
    def token(self):
        from workflow.credentials import get_token
        return self._token or get_token()
    def call(self,method,body=None):
        from workflow.runtime_config import require_operational_context
        require_operational_context()
        request=Request(self.url,data=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode('utf-8') if body is not None else None,
            headers={'Content-Type':'application/json','OAI-Sites-Authorization':'Bearer '+self.token},method=method)
        try:
            with build_opener(NoRedirect()).open(request,timeout=30) as response:return json.load(response)
        except HTTPError as exc:
            if exc.code==413:raise Held('Workflow capacity reached; existing data unchanged. Recover the spooled intake after expanding storage.') from exc
            if exc.code==401:raise Held('Private CRM credential rejected. Rotate/provision the dedicated Sites credential; no request was retried.') from exc
            if exc.code==409:raise Conflict('Remote revision changed') from exc
            raise
    def read(self):
        result=self.call('GET');self._baseline=copy.deepcopy(result['state'])
        state=migrate(result['state']);self._revision=result['revision']
        self._delta=result.get('storage_protocol')=='r2-versioned-delta-v1'
        return self._revision,state
    def save(self,revision,state):
        if self._delta:
            if self._revision!=revision or self._baseline is None:raise Conflict('Reread current state before saving changes')
            body={'revision':revision,'changes':state_changes(self._baseline,state)}
        else:body={'revision':revision,'state':state}
        result=self.call('PUT',body)
        self._revision=result['revision'];self._baseline=copy.deepcopy(state)

class Pipeline:
    def __init__(self,store):self.store=store
    def mutate(self,action,fn):
        rev,state=self.store.read();migrate(state);result=fn(state)
        state['events'].append({'at':time.time(),'action':action})
        self.store.save(rev,state);return result
    def candidate(self,value):
        def run(s):
            merged=copy.deepcopy(value)
            for key in ('requires_employer_visa_sponsorship','us_work_authorization','answer_provenance','application_drafting_context'):
                if key not in merged and key in s['candidate']:merged[key]=s['candidate'][key]
            s['candidate']=merged
        return self.mutate('candidate_import',run)
    def pause(self,paused=True):
        return self.mutate('pause' if paused else 'resume_dry_run',lambda s:s.update(paused=paused))
    def ingest(self,rows,cursor=None):
        def run(s):
            imported=[]
            for row in rows:
                url=canonical(row['url']);key='url:'+url
                req=row.get('requisition_id')
                reqkey='req:'+row['company'].casefold().strip()+':'+str(req) if req else None
                known={s['aliases'][k] for k in (key,reqkey) if k and k in s['aliases']}
                if len(known)>1:raise Held('Conflicting source identities require reconciliation')
                jid=next(iter(known),row.get('id') or 'job-'+sha(key)[:20])
                if jid in s['jobs'] and not known:raise Held('Existing job ID has a different source')
                if jid not in s['jobs']:
                    s['jobs'][jid]=dict(id=jid,company=row['company'],title=row['title'],state='capture_required',
                        sources=[],snapshots=[],attempts=[],documents=[],revisions=0,claim=None,screen=None,requisition_id=req,
                        tracking=copy.deepcopy(row.get('tracking',{})),possible_duplicates=[])
                    # Fuzzy identity never silently merges distinct requisitions.
                    fingerprint=(row['company'].casefold(),row['title'].casefold())
                    s['jobs'][jid]['possible_duplicates']=[j['id'] for j in s['jobs'].values() if j['id']!=jid and
                        (j['company'].casefold(),j['title'].casefold())==fingerprint]
                j=s['jobs'][jid]
                if row.get('application_route'):j['application_route']=copy.deepcopy(row['application_route'])
                j.setdefault('dimensions',{}).setdefault('source',row.get('source','unknown'))
                j['dimensions'].setdefault('referral',row.get('referral','unknown'))
                if url not in j['sources']:j['sources'].append(url)
                s['aliases'][key]=jid
                if reqkey:s['aliases'][reqkey]=jid
                if row.get('description'):
                    snapshot=dict(text=row['description'],source_url=url,checked_at=row['checked_at'],
                        posted_at=row.get('posted_at'),post_age=row.get('post_age'),evidence=row.get('evidence',[]))
                    snapshot['hash']=sha(snapshot['text'])
                    observation={k:v for k,v in snapshot.items() if k!='text'}
                    if observation not in j.setdefault('checks',[]):j['checks'].append(observation)
                    j['last_checked_at']=row['checked_at']
                    if not j['snapshots'] or j['snapshots'][-1]['hash']!=snapshot['hash']:
                        j['snapshots'].append(snapshot)
                        if not j['attempts']:
                            j['state']='captured';j['screen']=None
                imported.append(jid)
            if cursor:s['cursors'].update(cursor)
            return imported
        return self.mutate('ingest',run)
    def claim(self,jid,owner,ttl=300):
        def run(s):
            if s['paused']:raise Held('Queue is paused')
            require_application_route(s,jid)
            j=s['jobs'][jid];old=j['claim']
            if old and old['expires']>time.time():raise Held('Job already claimed')
            token=str(uuid.uuid4());j['claim']=dict(owner=owner,token=token,expires=time.time()+ttl)
            return token
        return self.mutate('claim',run)
    @staticmethod
    def owned(j,token):
        if not j['claim'] or j['claim']['token']!=token or j['claim']['expires']<=time.time():raise Held('Missing or stale claim')
    def evidence_refresh(self,vault,paths,cache):
        from workflow.evidence import EvidenceIndex
        indexer=EvidenceIndex(cache)
        try:index=indexer.build(vault,paths)
        finally:indexer.close()
        metrics={k:index.pop(k) for k in ('cache_hits','cache_misses')}
        def run(s):
            s['candidate']['evidence_index']=index
            return dict(fingerprint=index['fingerprint'],records=len(index['records']),**metrics)
        return self.mutate('evidence_refresh',run)
    def attest_capture(self,jid,token,extracted,independent,reviewer):
        """Trusted coordinator API, never populated from extraction JSON.

        Call only after a separately configured reviewer ran. A matching second
        answer is a cross-check, not a claim of perfect semantic verification.
        """
        from workflow.capture_review import review_body, semantic_issues, agreement_body, source_issues
        def run(s):
            j=s['jobs'][jid];self.owned(j,token)
            if j['attempts']:raise Held('Existing attempts require reconciliation')
            if not reviewer or reviewer == extracted.get('extractor'):raise Held('Separate capture reviewer required')
            first,second=review_body(extracted),review_body(independent)
            passed=(first is not None and second is not None
                    and sha(agreement_body(extracted))==sha(agreement_body(independent))
                    and not semantic_issues(extracted) and not semantic_issues(independent)
                    and not source_issues(j,extracted) and not source_issues(j,independent))
            j['capture_attestation']=dict(snapshot_hash=j['snapshots'][-1]['hash'],body_hash=sha(first),
                independent_hash=sha(second),reviewer=reviewer,passed=passed,policy_version=VERSION,at=time.time())
            return passed
        return self.mutate('independent_capture_review',run)
    def screen(self,jid,token,facts):
        def run(s):
            j=s['jobs'][jid];self.owned(j,token)
            if j['attempts']:raise Held('Existing attempts must be reconciled before new preparation')
            if not j['snapshots']:raise Held('Capture required before screening')
            from workflow.evidence import current
            from workflow.dependencies import screen_keys, candidate_binding
            keys=screen_keys(facts); binding=candidate_binding(s['candidate'],keys)
            old=j.get('screen') or {}
            from workflow.target_policy import policy_digest
            selection=s.get('application_selection_policy')
            selection_hash=policy_digest(selection) if isinstance(selection,dict) else None
            key=sha([VERSION,j['snapshots'][-1],binding,facts,j.get('capture_attestation'),selection_hash])
            if old.get('cache_key')==key and current(s['candidate'].get('evidence_index',{}),keys):
                # Recheck time-sensitive evidence without another model/retrieval call.
                result=evidence_screen(j,s['candidate'],facts,selection_policy=s.get('application_selection_policy'))
                result['cache_hit']=True
            else:
                result=evidence_screen(j,s['candidate'],facts,selection_policy=s.get('application_selection_policy'));result['cache_hit']=False
            if not current(s['candidate'].get('evidence_index',{}),keys):
                result['questions'].append('evidence_index_stale')
                if result['decision']=='eligible':result['decision']='needs_input'
            result.update(snapshot_hash=j['snapshots'][-1]['hash'],candidate_hash=binding,dependency_keys=keys,
                          facts=copy.deepcopy(facts),cache_key=key,selection_policy_hash=selection_hash)
            j['screen']=result;j['state']={'eligible':'screened','needs_input':'needs_input','excluded':'excluded'}[result['decision']]
            j.setdefault('dimensions',{}).update({k:result.get(k,'unknown') for k in ('role_family','sponsorship','seniority')})
            return result
        return self.mutate('screen',run)
    def package(self,jid,answers,contract=None):
        from workflow.safeguards import package
        _,s=self.store.read();j=s['jobs'][jid];require_screen(s,j)
        return package(s,j,answers,contract)
    def outcome(self,jid,stage,evidence,at,event_id):
        if stage not in ('submitted','interview','assessment','offer','rejected','withdrawn'):raise Held('Unknown funnel stage')
        if not evidence or not at or not event_id:raise Held('Dated outcome evidence and stable event ID required')
        def run(s):
            j=s['jobs'][jid];events=j.setdefault('funnel_events',[])
            event=dict(id=event_id,stage=stage,evidence=copy.deepcopy(evidence),at=at)
            if stage=='submitted':event['dimensions']=copy.deepcopy(j.get('dimensions',{}))
            prior=next((e for e in events if e['id']==event_id),None)
            if prior and prior!=event:raise Held('Conflicting outcome identity')
            if not prior:events.append(event)
            return event
        return self.mutate('outcome',run)
    def analytics(self):
        from workflow.safeguards import analytics
        return analytics(self.store.read()[1])
    def document(self,jid,token,doc):
        incoming=doc
        def run(s):
            j=s['jobs'][jid];self.owned(j,token)
            if j['state'] not in ('screened','revision_required'):raise Held('Job not ready for drafting')
            require_screen(s,j);validate_document(s['candidate'],incoming)
            if s['candidate'].get('cv_policy')=='single_master_no_tailoring' and incoming.get('pdf_sha256')!=s['candidate'].get('master_pdf_sha256'):
                raise Held('Only the canonical master CV may be attached')
            doc=copy.deepcopy(incoming);doc['id']=sha(doc);doc['job_id']=jid
            from workflow.dependencies import document_dependencies, candidate_binding
            keys,ids=document_dependencies(doc)
            doc['snapshot_hash']=j['snapshots'][-1]['hash'];doc['candidate_hash']=candidate_binding(s['candidate'],keys,ids);doc['review']=None
            s['documents'][doc['id']]=doc;j['documents'].append(doc['id']);j['state']='drafted'
            return doc['id']
        return self.mutate('document',run)
    def review(self,jid,token,reviewer,result):
        def run(s):
            j=s['jobs'][jid];self.owned(j,token)
            if j['state']!='drafted':raise Held('No draft awaiting review')
            require_screen(s,j)
            doc=s['documents'][j['documents'][-1]]
            if reviewer==doc['writer']:raise Held('Reviewer must be independent of writer')
            if doc['snapshot_hash']!=j['snapshots'][-1]['hash']:raise Held('Job description changed')
            doc['review']=dict(result,reviewer=reviewer,at=time.time())
            j['state']='reviewed' if result.get('passed') is True else 'review_failed'
            return j['state']
        return self.mutate('review',run)
    def revision(self,jid,token,issue=None):
        def run(s):
            j=s['jobs'][jid];self.owned(j,token)
            if j['state']!='review_failed':raise Held('Revision requires failed review')
            if j['revisions']>=2 or (j['revisions']==1 and not issue):raise Held('Revision cap or specific unresolved issue missing')
            if j['revisions']==1:
                issues=s['documents'][j['documents'][-1]]['review'].get('issues',[])
                if issue not in issues:raise Held('Second revision must name an unresolved reviewer issue')
            j['revisions']+=1;j['state']='revision_required'
        return self.mutate('revision',run)
    def prepare(self,jid,token):
        def run(s):
            if s['paused']:raise Held('Queue is paused')
            from workflow.mail_sync import require_reservation
            require_reservation(s,jid)
            j=s['jobs'][jid];self.owned(j,token)
            if j['state']!='reviewed':raise Held('Independent review required')
            require_screen(s,j)
            validate_document(s['candidate'],s['documents'][j['documents'][-1]])
            if j['screen']['decision']!='eligible':raise Held('Candidate or eligibility answers unresolved')
            doc=s['documents'][j['documents'][-1]]
            if doc.get('held') or doc['review'].get('semantic_passed') is not True:raise Held('Document or semantic fit review held')
            if j['attempts']:raise Held('Existing attempt must be reconciled; no blind retry')
            if s['browser']:raise Held('Browser is owned; explicit reconciliation required')
            attempt=dict(id=str(uuid.uuid4()),mode='dry_run',status='prepared',created_at=time.time())
            j['attempts'].append(attempt);j['state']='attempt_prepared'
            s['browser']=dict(job_id=jid,attempt_id=attempt['id'],token=token)
            return attempt['id']
        return self.mutate('prepare_attempt',run)
    def finish(self,jid,attempt_id,outcome,evidence):
        def run(s):
            j=s['jobs'][jid]
            if not j['attempts'] or j['attempts'][-1]['id']!=attempt_id:raise Held('Attempt not found')
            a=j['attempts'][-1]
            if a.get('mode')!='dry_run':raise Held('Only dry-run attempts may use finish; rehearsal requires guarded Pilot review')
            if a['status']!='prepared':raise Held('Attempt already settled; reconcile separately')
            if outcome not in ('dry_run_complete','uncertain'):raise Held('Live submission is disabled')
            if not evidence:raise Held('Outcome evidence required')
            a.update(status=outcome,evidence=evidence,finished_at=time.time());j['state']=outcome
            # Unknown outcomes retain browser ownership until explicit reconciliation.
            if outcome=='dry_run_complete':s['browser']=None
        return self.mutate('finish_attempt',run)
    def reconcile(self,jid,evidence):
        def run(s):
            j=s['jobs'][jid]
            if j['state'] not in ('uncertain','attempt_prepared'):raise Held('No unresolved attempt')
            if not evidence:raise Held('Reconciliation evidence required')
            j['attempts'][-1].update(status='reconciled_held',reconciliation=evidence)
            j['state']='reconciled_held';s['browser']=None
        return self.mutate('reconcile',run)
