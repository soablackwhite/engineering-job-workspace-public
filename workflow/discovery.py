"""Bounded public LinkedIn discovery/capture. Authentication walls stop the run."""
import argparse, datetime as dt, json, re
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from workflow.engine import canonical

SEARCHES={
 'applied_ai':'applied AI engineer AI platform engineer',
 'product_fde':'product engineer forward deployed AI engineer'}
class Page(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.scripts=[];self.script=False;self.buffer='';self.text=[];self.fields={};self.active=[];self.depth=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag not in ('br','hr','img','meta','link','input','source','wbr'):self.depth+=1
        for key in ('show-more-less-html__markup','top-card-layout__title','topcard__org-name-link','posted-time-ago__text'):
            if key in (a.get('class') or '').split():self.active.append((key,self.depth));self.fields[key]=[]
        if tag=='a' and '/jobs/view/' in a.get('href',''):self.links.append(a['href'])
        if tag=='script' and a.get('type')=='application/ld+json':self.script=True;self.buffer=''
    def handle_endtag(self,tag):
        if tag=='script' and self.script:self.scripts.append(self.buffer);self.script=False
        self.active=[v for v in self.active if v[1]!=self.depth]
        if tag not in ('br','hr','img','meta','link','input','source','wbr'):self.depth-=1
    def handle_data(self,data):
        for key,_ in self.active:self.fields[key].append(data)
        if self.script:self.buffer+=data
        else:self.text.append(data)

def fetch(url):
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=25) as r:
        if r.status!=200:raise RuntimeError('Capture unavailable: '+str(r.status))
        raw=r.read(2_000_001)
        if len(raw)>2_000_000:raise RuntimeError('Page exceeds capture limit')
    p=Page();p.feed(raw.decode('utf-8'));return p

def capture(url):
    page=fetch(url);jobs=[]
    for raw in page.scripts:
        try:
            data=json.loads(raw)
            nodes=data if isinstance(data,list) else data.get('@graph',[data])
            jobs.extend(n for n in nodes if n.get('@type')=='JobPosting')
        except (ValueError,AttributeError):continue
    if not jobs and page.fields.get('show-more-less-html__markup'):
        fields={k:' '.join(' '.join(v).split()) for k,v in page.fields.items()}
        text=fields['show-more-less-html__markup']
        if len(text)<250:raise RuntimeError('Incomplete public description')
        return dict(url=canonical(url),company=fields['topcard__org-name-link'],title=fields['top-card-layout__title'],description=text,checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),posted_at=None,post_age=fields.get('posted-time-ago__text'),evidence=[{'kind':'public_description_html','source_url':canonical(url)}])
    if len(jobs)!=1:raise RuntimeError('No complete public JobPosting found; authenticated browser capture needed')
    j=jobs[0];description=Page();description.feed(j['description'])
    text='\n'.join(x.strip() for x in description.text if x.strip())
    if len(text)<250:raise RuntimeError('Description is incomplete')
    return dict(url=canonical(url),company=j['hiringOrganization']['name'],title=j['title'],
        description=text,checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        posted_at=j.get('datePosted'),post_age=None,
        evidence=[{'kind':'public_jobposting_jsonld','source_url':canonical(url)}])

def discover(limit=10):
    urls=[]
    for keywords in SEARCHES.values():
        url='https://www.linkedin.com/jobs/search/?'+urlencode(dict(keywords=keywords,location='United States',f_TPR='r21600',sortBy='DD',f_AL='true'))
        for link in fetch(url).links:
            try:normalized=canonical(link)
            except ValueError:continue
            if normalized not in urls:urls.append(normalized)
    if not urls:raise RuntimeError('No public search results; login/browser capture needed')
    return urls[:limit]

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url');p.add_argument('--output',required=True);p.add_argument('--limit',type=int,default=10);a=p.parse_args()
    rows=[capture(u) for u in ([a.url] if a.url else discover(a.limit))]
    from pathlib import Path
    Path(a.output).write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'captured':len(rows),'output':a.output}))
