"""Read-only Gmail adapter input -> deterministic proposals -> verified CRM updates.

No Gmail credentials, browser actions, email sends, or application submissions.
Ambiguous messages enter a durable review queue. Retrieval coverage is deliberately
separate from the legacy submission gate's all-history reconciliation checkpoint.
"""
from __future__ import annotations
import argparse
import copy
from collections import Counter
from datetime import datetime, timezone, timedelta
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from workflow.engine import Held, RemoteStore
from workflow.credentials import TARGET, read_owner_credential
from workflow.mail_sync import receipt_account, apply_event, digest, job_url, normalize

VERSION = 1
QUERIES = {
 'application_and_ats': 'in:anywhere {subject:"application" subject:"candidature" subject:"interview" from:jobs-noreply@linkedin.com from:greenhouse.io from:greenhouse-mail.io from:lever.co from:ashbyhq.com from:myworkday.com from:myworkdayjobs.com from:workablemail.com from:smartrecruiters.com from:idibu.com from:gem.com from:yello.co} -subject:"job alert"',
 'decision_language': 'in:anywhere {"other candidates" "not selected" "not proceed" "not be progressing" "not moving forward" "unsuccessful" "regret to inform" "ne pas donner suite" "candidature non retenue"}',
 'additional_ats_and_assessments': 'in:anywhere {subject:assessment subject:"coding challenge" subject:"technical challenge" subject:"received your resume" subject:"received your résumé" subject:"interview invitation" from:applytojob.com from:jazzhr.com from:icims.com from:taleo.net from:successfactors.com subject:"Application delivery"} -subject:"job alert"',
}
STOP = re.compile(r'Top jobs looking|Jobs you may be interested|Explore similar jobs|This email was intended|Get the new LinkedIn desktop app', re.I)

def clean(value):
    return ' '.join(re.sub(r'[\u034f\u200b-\u200f\ufeff]', '', unescape(value or '')).split())

class MailHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.text=[]; self.links=[]; self.anchor=None; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.skip += 1
        if tag in ('p','div','br','tr','td'): self.text.append('\n')
        if tag == 'a': self.anchor={'url':dict(attrs).get('href',''),'label':[], 'offset':len(''.join(self.text))}
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
        if tag == 'a' and self.anchor:
            self.anchor['label']=clean(''.join(self.anchor['label'])); self.links.append(self.anchor); self.anchor=None
        if tag in ('p','div','tr','td'): self.text.append('\n')
    def handle_data(self, value):
        if not self.skip:
            self.text.append(value)
            if self.anchor: self.anchor['label'].append(value)

def mime_parts(payload, kind):
    content=payload.get('body',{}).get('content','') if payload.get('mime_type') == kind else ''
    return '\n'.join([content, *[mime_parts(p,kind) for p in payload.get('parts',[])]])

def normalize_message(raw):
    """Always read BOTH MIME alternatives; LinkedIn plain text can omit decisions."""
    payload=raw.get('payload',{}); headers={h['name'].lower():h['value'] for h in payload.get('headers',[])}
    plain=raw.get('text') if 'text' in raw else mime_parts(payload,'text/plain')
    html=raw.get('html') if 'html' in raw else mime_parts(payload,'text/html')
    parser=MailHTML(); parser.feed(html or '')
    html_text=''.join(parser.text); boundary=STOP.search(html_text)
    limit=boundary.start() if boundary else len(html_text)
    links=[l for l in parser.links if l['offset'] < limit]
    # The recommendation footer contains other job IDs. Never bind those.
    urls=[]
    for l in links:
        u=l['url']; host=(urlsplit(u).hostname or '').lower()
        if host in ('linkedin.com','www.linkedin.com') and re.search(r'/jobs/view/',u):
            urls.append({'url':job_url(u),'label':l['label']})
    if not urls:
        for u in re.findall(r'https?://[^\s<>\"]+', STOP.split(plain or '')[0]):
            if (urlsplit(u).hostname or '').endswith('linkedin.com') and '/jobs/view/' in u:
                urls.append({'url':job_url(u),'label':''})
    stamp=raw.get('date') or datetime.fromtimestamp(int(raw['internal_date'])/1000,timezone.utc).isoformat()
    stamp=datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
    body=clean(STOP.split(plain or '')[0])+'\n'+clean(html_text[:limit])
    body=re.sub(r'https?://[^\s<>\"]+', '[link]', body)
    body=re.split(r'\*\* PERSONAL INFORMATION \*\*|PERSONAL INFORMATION',body)[0]
    return dict(id=raw['id'],thread_id=raw['thread_id'],date=stamp,
                subject=clean(raw.get('subject',headers.get('subject',''))),
                sender=parseaddr(raw.get('from',headers.get('from','')))[1].lower(),
                text=body,links=urls)

def classify(m):
    text=m['text']; subject=m['subject']; sender=m['sender']
    if not text.strip():
        return 'review','No readable MIME body; do not infer from subject alone'
    if sender == receipt_account():
        return 'ignored','Outgoing message; quoted decisions are not new employer decisions'
    if re.search(r'visa application|student admission|course enrollment|training registration',subject,re.I):
        return 'ignored','Education, visa, or training application, not employment'
    if subject.lower().startswith(('re:','fw:','fwd:')):
        return 'review', 'Reply/forward requires original-message review'
    if re.search(r'problem (?:with|completing).*application|application delivery',subject+' '+text,re.I):
        return 'delivery_failed','Explicit application delivery problem'
    if re.search(r'not (?:be )?moving forward with your application|not selected to proceed|decided (?:not to move forward|to move forward with other candidates)|candidature.*non retenue',text,re.I):
        return 'rejected','Explicit negative decision in message body'
    if re.search(r'invite you to the next stage.*assessment|invite you.*assessment|you are invited to (?:optionally )?(?:take|complete) (?:an? |the |initial )?assessment|your application.{0,180}has reached the assessment stage',text,re.I):
        return 'assessment','Explicit assessment invitation (not a completed interview)'
    if re.search(r'(?:invite|inviting|schedule|scheduling) (?:you|an|a|your).*interview',text,re.I):
        return 'interview','Interview invitation; identity still required'
    if subject.lower().startswith('your application was viewed'):
        return 'viewed','Employer view; does not imply interview'
    if re.search(r'add the tools|complete your profile',subject,re.I):
        return 'review','Profile completion request; not sufficient submission evidence'
    if re.search(r'application was sent to|thank(?:s| you) for applying|application (?:has been )?received|(?:we.{0,8}ve|we) received your application',subject+' '+text,re.I):
        return 'submitted','Receipt language; identity still required'
    if re.search(r'application|applying|candidature',subject+' '+text,re.I) and any(x in sender for x in ('greenhouse','lever.co','workable','ashby','idibu','yello.co','getwingapp')):
        return 'review','ATS or recruiting message requires interpretation'
    # Broad discovery is intentionally review-only: a declined payment, school
    # admission or quoted rejection must never automatically reject a job.
    if re.search(r'\breject(?:ed|ion)?\b|unfortunately|regret|unsuccessful|other candidates|other applicants|not.{0,35}(?:forward|progress|proceed|selected|shortlist)|unable to offer|position.{0,25}(?:filled|closed)|malheureusement|pas.{0,25}(?:suite|reten|poursuiv)|non reten|refus|désol|lamentamos|infelizmente|للأسف|نعتذر',subject+' '+text,re.I):
        return 'review','Possible negative decision; employment context and original decision require review'
    if re.search(r'\b(?:recruit(?:er|ing|ment)|hiring|candidature|candidacy|job application|your application|your resume|your résumé)\b',subject+' '+text,re.I):
        return 'review','Employment-related message outside known templates; inspect before ignoring'
    return 'ignored','No supported employment lifecycle signal'

def identity(m, state, override=None):
    body=m['text']; subject=m['subject']; title=company=None
    match=re.match(r'Your application to (.+) at (.+)$',subject)
    if match: title,company=map(clean,match.groups())
    links=m['links']
    if title:
        exact=[l for l in links if normalize(l['label']) == normalize(title)]
        if exact: links=exact
    urls=sorted(set(l['url'] for l in links))
    if override:
        job=state['jobs'].get(override['job_id'])
        if not job or not override.get('reason'): raise Held('Reviewed binding needs a real job and evidence reason')
        return override['job_id'], job['company'], job['title'], job['sources'][0], override['reason']
    # A named client in an agency receipt may be a separate application. Do not
    # silently map the client to the agency based on a footer mention.
    if re.search(r'Application Received .* @ ',subject,re.I) and not urls:
        return None,None,None,None,'Client/agency relationship requires reviewed binding'
    if len(urls)>1:
        return None,None,None,None,'Conflicting primary job links; do not fall back to text matching'
    if len(urls)==1:
        matches=[(jid,j) for jid,j in state['jobs'].items() if urls[0] in [job_url(u) for u in j.get('sources',[])]]
        if len(matches)==1:
            jid,j=matches[0]; return jid,j['company'],j['title'],urls[0],'Exact primary job link; recommendation footer excluded'
        # New LinkedIn receipt: use the linked job title and subject company.
        if not company:
            match=re.search(r'application was sent to (.+?)[!.]?$',subject,re.I)
            if match: company=match.group(1)
        if not title:
            labels=[l['label'] for l in links if l['url']==urls[0] and l['label'].lower() not in ('view job','view application','see application')]
            if labels: title=labels[0]
        if not matches and title and company:
            return None,company,title,urls[0],'Exact primary LinkedIn job link and receipt title/company'
    # Exact role and employer in the body, unique among known roles. Never employer-only fuzzy binding.
    norm_body=' '+normalize(subject+' '+body)+' '
    candidates=[]
    for jid,j in state['jobs'].items():
        names=[j['company'],*j.get('employer_aliases',[])]
        if (' '+normalize(j['title'])+' ') in norm_body and any((' '+normalize(n)+' ') in norm_body for n in names):
            candidates.append((jid,j))
    if len(candidates)==1:
        jid,j=candidates[0];return jid,j['company'],j['title'],j['sources'][0],'Unique exact employer/alias and role text in message'
    return None,None,None,None,'No unambiguous primary link or unique exact employer + role'

def propose(messages, state, overrides=None):
    events=[]; review=[]; ignored=[]; existing=[]; views=[]
    for raw in sorted(messages,key=lambda m:m['date']):
        m=normalize_message(raw); key=receipt_account()+':'+m['id']
        if key in state.get('receipt_events',{}): existing.append(m['id']);continue
        status,reason=classify(m); override=(overrides or {}).get(m['id'])
        if override and override.get('status'):
            if not override.get('reason'):raise Held('Reviewed decision needs evidence reason')
            status=override['status']
            reason=override['reason']
        if status=='ignored':ignored.append({'id':m['id'],'reason':reason});continue
        jid,company,title,url,basis=identity(m,state,override)
        if status=='review' or not url:
            review.append({'id':m['id'],'thread_id':m['thread_id'],'subject':m['subject'],
                           'date':m['date'],'classification':status,'reason':reason+'; '+basis,
                           'excerpt':clean(m['text'])[:900]});continue
        event=dict(source='gmail_connector',account=receipt_account(),message_id=m['id'],thread_id=m['thread_id'],
                   status=status,company=company,title=title,job_url=job_url(url),event_at=m['date'],
                   excerpt=clean(m['text'])[:1400],identity_basis=basis)
        if status=='submitted': event['application_date']=m['date'][:10]
        if status=='viewed': views.append({'job_id':jid,'event':event})
        else: events.append(event)
    return dict(events=events,review=review,ignored=ignored,existing=existing,views=views)

def validate_manifest(data):
    if data.get('account') != receipt_account(): raise Held('Wrong Gmail account')
    if data.get('query_version') != VERSION: raise Held('Wrong ingestion query version')
    if not data.get('through') or not data.get('queries'): raise Held('Fixed coverage bounds required')
    upper=datetime.fromisoformat(data['through'].replace('Z','+00:00'))
    if upper.tzinfo is None:raise Held('Coverage boundary needs a timezone')
    ids=set(); messages={m['id']:m for m in data['messages']}
    for lane in data['queries']:
        pages=lane.get('pages',[])
        if not pages or pages[-1].get('next_page_token'): raise Held('Search pagination incomplete')
        if any(not p.get('next_page_token') for p in pages[:-1]): raise Held('Broken pagination chain')
        if lane.get('errors'): raise Held('Unresolved search errors')
        bounds=re.findall(r'\bbefore:(\d+)\b',lane['query'])
        if bounds != [str(int(upper.timestamp()))]: raise Held('Query upper bound does not match coverage timestamp')
        ids.update(i for p in pages for i in p['ids'])
    if not ids <= messages.keys(): raise Held('Unread searched IDs; checkpoint cannot advance')
    if len(messages)!=len(data['messages']): raise Held('Duplicate message records')
    for m in data['messages']:
        if not m.get('thread_id') or not m.get('date'): raise Held('Incomplete message provenance')
        stamp=datetime.fromisoformat(m['date'].replace('Z','+00:00'))
        if stamp.tzinfo is None or stamp >= upper: raise Held('Message outside fixed scan boundary')
    return ids

def reconcile(store, data, overrides=None, dry_run=False):
    ids=validate_manifest(data); revision,state=store.read(); before=copy.deepcopy(state)
    proposal=propose(data['messages'],state,overrides)
    run_id=digest({'version':VERSION,'through':data['through'],'queries':data['queries']})
    sync=state.setdefault('email_ingestion',{'runs':{},'review_queue':{},'messages':{}})
    for event in proposal['events']: apply_event(state,event)
    for item in proposal['views']:
        if not item['job_id']:
            proposal['review'].append({'id':item['event']['message_id'],'reason':'Viewed message has no existing row'});continue
        e=item['event'];key=receipt_account()+':'+e['message_id'];j=state['jobs'][item['job_id']]
        j.setdefault('application',{})['last_viewed_at']=max(j.get('application',{}).get('last_viewed_at',''),e['event_at'])
        state.setdefault('receipt_events',{})[key]={'event':e,'outcome':{'status':'reconciled','job_id':item['job_id'],'lifecycle':'viewed'}}
    review_ids={r['id'] for r in proposal['review']}
    for item in proposal['review']:sync['review_queue'][item['id']]=item
    for mid in ids-review_ids:sync['review_queue'].pop(mid,None)
    for mid in ids:sync['messages'][mid]={'disposition':'review' if mid in review_ids else 'processed','query_version':VERSION}
    full_mailbox = len(data['queries']) == 1 and bool(re.fullmatch(r'in:anywhere(?: after:\d+)? before:\d+',data['queries'][0]['query']))
    sync['runs'][run_id]={'through':data['through'],'query_version':VERSION,'queries':data['queries'],
                         'retrieved':len(ids),'pending_review':len(review_ids),'scope': 'entire mailbox within query date bounds; automated triage with explicit review queue' if full_mailbox else 'targeted personal Gmail queries; not entire mailbox'}
    # Retrieval coverage can advance with an explicit review queue. Never claim the
    # separate legacy all-history identity/backfill submission gate is complete.
    sync['retrieved_through']=max(sync.get('retrieved_through',''),data['through'])
    state['paused']=True
    changed=[{'job_id':jid,'company':j['company'],'title':j['title'],
              'before':before['jobs'].get(jid,{}).get('state'),'after':j['state']}
             for jid,j in state['jobs'].items() if before['jobs'].get(jid,{}).get('state')!=j['state']]
    result={'revision_before':revision,'dry_run':dry_run,'searched_messages':len(ids),
            'new_events':len(proposal['events']),'review':proposal['review'],
            'changed_jobs':changed,'counts':dict(Counter(j['state'] for j in state['jobs'].values())),
            'legacy_backfill_advanced':False}
    if dry_run:return result
    if state != before:
        store.save(revision,state)
        new_revision,verified=store.read()
        if verified != state:raise Held('Exact reconciliation readback failed; do not claim completion')
    else:new_revision=revision
    result['revision_after']=new_revision;result['verified']=True
    return result

def scan_plan(checkpoint=None, through=None, full_mailbox=False, since=None):
    through=through or datetime.now(timezone.utc).isoformat()
    upper=int(datetime.fromisoformat(through.replace('Z','+00:00')).timestamp())
    lower=''
    if checkpoint:lower=' after:'+str(int((datetime.fromisoformat(checkpoint.replace('Z','+00:00'))-timedelta(hours=72)).timestamp()))
    if full_mailbox:
        if checkpoint and since: raise Held('Choose either checkpoint overlap or explicit since')
        if since:
            start=datetime.fromisoformat(since.replace('Z','+00:00'))
            if start.tzinfo is None or start.timestamp() >= upper:raise Held('Since requires a timezone and must precede through')
            lower=' after:'+str(int(start.timestamp())-1)
        return {'account':receipt_account(),'query_version':VERSION,'through':through,
                'queries':[{'name':'entire_mailbox','query':'in:anywhere'+lower+' before:'+str(upper)}]}
    if since:raise Held('Since is supported for unfiltered mailbox scans only')
    return {'account':receipt_account(),'query_version':VERSION,'through':through,
            'queries':[{'name':k,'query':q+lower+' before:'+str(upper)} for k,q in QUERIES.items()]}

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('plan');p.add_argument('--checkpoint');p.add_argument('--through');p.add_argument('--full-mailbox',action='store_true');p.add_argument('--since')
    p=sub.add_parser('reconcile');p.add_argument('--input',required=True);p.add_argument('--reviewed');p.add_argument('--snapshot');p.add_argument('--apply',action='store_true');p.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.action=='plan':print(json.dumps(scan_plan(args.checkpoint,args.through,args.full_mailbox,args.since)));return
    data=json.loads(Path(args.input).read_text()); overrides=json.loads(Path(args.reviewed).read_text()) if args.reviewed else {}
    if args.snapshot:
        if args.apply:raise Held('A snapshot is read-only; apply requires live CRM')
        class Snapshot:
            def read(self):
                x=json.loads(Path(args.snapshot).read_text());return x['revision'],x['state']
        store=Snapshot()
    else:store=RemoteStore(TARGET,token=read_owner_credential())
    result=reconcile(store,data,overrides,dry_run=not args.apply)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('review','changed_jobs')}))

if __name__=='__main__':main()
