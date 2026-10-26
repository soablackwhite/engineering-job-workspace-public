import {jobs as researchJobs,jobFields,emptyTracking,statuses,type Job,type Tracking} from './crm';

type WorkflowJob={id:string;company:string;title:string;sources?:string[];state:string;tracking?:Partial<Tracking>;application?:{status?:string;application_date?:string;lifecycle_event_at?:string;recorded_at?:string};snapshots?:{checked_at?:string;location?:string}[];draft_progress?:{missing?:string[]}};
export type WorkflowData={jobs?:Record<string,WorkflowJob>;paused?:boolean};
export function canonicalJobUrl(value:string){
 try{const u=new URL(value);const id=u.hostname.replace(/^www\./,'')==='linkedin.com'&&u.pathname.match(/\/jobs\/view\/(\d+)/)?.[1];if(id)return `linkedin:${id}`;
 u.hash='';for(const key of [...u.searchParams.keys()])if(/^(utm_|trk$|trackingId$|ref$)/i.test(key))u.searchParams.delete(key);return u.toString().replace(/\/$/,'');}catch{return value;}
}
function stage(j:WorkflowJob){
 if(j.application?.status&&statuses.includes(j.application.status))return j.application.status;
 const labels:Record<string,string>={applied:'Applied',rejected:'Rejected',interviewing:'Interviewing',needs_input:'Needs input',blocked:'Blocked',excluded:'Excluded',shortlisted:'Interested'};
 return labels[j.state];
}
function campaignJob(j:WorkflowJob):Job{
 const row=Object.fromEntries(jobFields.map(k=>[k,'Not specified'])) as Job;
 const checked=j.snapshots?.at(-1)?.checked_at?.slice(0,10)||j.application?.application_date||'';
 return {...row,id:j.id,Company:j.company,Role:j.title,Category:'Campaign opportunity','Evidence tier':'Sponsorship unknown','Job / apply link':j.sources?.[0]||'','Company LinkedIn discovery':'','People / connections discovery':'','Checked date':checked,'Application status':stage(j)||'To review','Verification / caveat':j.draft_progress?.missing?.join('; ')||'Campaign record. See application queue for evidence and screening.'};
}
export function projectWorkspace(workflow:WorkflowData,manual:(Tracking&{id:string})[],catalog:Job[]=researchJobs){
 const jobs=catalog.map(j=>({...j}));const byId=new Map(jobs.map(j=>[j.id,j]));const byUrl=new Map<string,Job>();
 for(const j of jobs)byUrl.set(canonicalJobUrl(j['Job / apply link']),j);
 const raw=new Map(manual.map(t=>[t.id,t]));const tracking:Record<string,Tracking>={};const workflowIds:Record<string,string>={};
 for(const j of jobs)tracking[j.id]={...emptyTracking,...raw.get(j.id)};
 for(const w of Object.values(workflow.jobs||{})){
  const matches=[...new Set((w.sources||[]).map(u=>byUrl.get(canonicalJobUrl(u))).filter(Boolean))] as Job[];
  // Exact source identity only: never merge same-company roles by title similarity.
  let j=byId.get(w.id)||(matches.length===1?matches[0]:undefined);
  if(!j){j=campaignJob(w);jobs.push(j);byId.set(j.id,j);for(const u of w.sources||[])byUrl.set(canonicalJobUrl(u),j);}
  workflowIds[w.id]=j.id;
  const candidates=[raw.get(j.id),raw.get(w.id)].filter(Boolean) as (Tracking&{id:string})[];
  const saved=candidates.sort((a,b)=>(b.updatedAt||'').localeCompare(a.updatedAt||''))[0];
  const t:Tracking={...emptyTracking,...w.tracking,...saved};
  const status=stage(w);const eventAt=w.application?.lifecycle_event_at||w.application?.recorded_at||w.application?.application_date;
  const newerManual=saved?.updatedAt&&eventAt&&Date.parse(saved.updatedAt)>Date.parse(eventAt);
  if(status&&!newerManual)t.status=status;
  tracking[j.id]=t;
 }
 return {jobs,tracking,workflowIds,paused:workflow.paused??true};
}
