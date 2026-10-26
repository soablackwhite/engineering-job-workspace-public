function stable(value:any):string{return JSON.stringify(value,(_key,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);}
const rehearsalStatuses=['prepared','open_pending','opened','fill_pending','filled','upload_pending','uploaded','form_reviewed','uncertain','reconciled_held'];
export function emptyWorkflow(){return {schema_version:2,mode:'dry_run',paused:true,jobs:{},aliases:{},browser:null,candidate:{},documents:{},events:[],cursors:{}};}
export type Workflow = ReturnType<typeof emptyWorkflow>;
// Fail closed at the server boundary. Python stage operations enforce worker transitions.
export function validateState(next:any,previous:any,liveEnabled=false){
 if(!next||!([1,2].includes(next.schema_version))||!(['dry_run','presubmit_rehearsal',...(liveEnabled||previous.mode==='supervised_pilot'?['supervised_pilot']:[])].includes(next.mode))||typeof next.paused!=='boolean'||!next.jobs||!next.aliases||!next.documents||!Array.isArray(next.events))throw Error('Invalid workflow state');
 if(previous.schema_version===2&&next.schema_version!==2)throw Error('Workflow schema cannot be downgraded');
 for(const [hash,bytes] of Object.entries(previous.artifacts||{}))if(next.artifacts?.[hash]!==bytes)throw Error('Archived artifacts are immutable');
 for(const [id,job] of Object.entries(next.jobs) as [string,any][]){
  for(const a of job.attempts||[]){
   const isNew=!(previous.jobs?.[id]?.attempts||[]).some((old:any)=>old.id===a.id);
   if(next.schema_version===2&&isNew&&['supervised_pilot','presubmit_rehearsal'].includes(a.mode)&&(!a.package?.sha256||a.package?.version!=='evidence-gated-2'||!a.submission_context||job.screen?.policy_version!=='evidence-gated-2'||job.screen?.decision!=='eligible'))throw Error('Evidence-gated package required');
   if(a.mode==='presubmit_rehearsal'){
    if(next.schema_version!==2||a.grant?.allow_submit===true||!rehearsalStatuses.includes(a.status))throw Error('Rehearsal cannot submit');
    if(isNew&&(a.status!=='prepared'||next.mode!=='presubmit_rehearsal'))throw Error('Rehearsal must start prepared');
    if(a.grant?.package_sha256!==a.package?.sha256||a.grant?.package_version!==a.package?.version||a.package?.payload?.job_id!==id||a.submission_context?.artifact_sha256!==a.pdf_sha256||a.package?.payload?.snapshot_hash!==a.grant?.snapshot_hash||a.grant?.pdf_sha256!==a.pdf_sha256||a.grant?.document_id!==a.document_id)throw Error('Rehearsal package binding required');
    const contract={...a.grant};delete contract.package_sha256;delete contract.package_version;
    if(stable(contract)!==stable(a.package.payload.contract)||stable(a.answers)!==stable(a.package.payload.answers)||a.candidate_hash!==a.package.payload.candidate_hash||a.answers_sha256!==a.grant.answers_sha256)throw Error('Rehearsal answers and contract must match package');
    if(!a.answers||!Object.keys(a.answers).length||Object.entries(a.answers).some(([key,value])=>stable(value)!==stable(a.grant.fields?.[key]?.value)))throw Error('Rehearsal requires exact field answers');
    if(a.status==='form_reviewed'&&(!a.review_sha256||a.review_evidence?.mode!=='presubmit_rehearsal'||a.review_evidence?.package_sha256!==a.package.sha256||a.review_evidence?.pdf_sha256!==a.pdf_sha256||stable(a.review_evidence?.answers)!==stable(a.answers)))throw Error('Rehearsal review evidence required');
    if((job.funnel_events||[]).some((e:any)=>e.id===a.id&&e.stage==='submitted'))throw Error('Rehearsal cannot record submission');
   }
  }
  if(job.id!==id||!Array.isArray(job.attempts)||!Array.isArray(job.snapshots)||!Array.isArray(job.documents)||job.revisions<0||job.revisions>2)throw Error('Invalid job');
  if(job.attempts.length>1||job.attempts.some((a:any)=>a.mode==='dry_run'?!['prepared','dry_run_complete','uncertain','reconciled_held'].includes(a.status):a.mode==='presubmit_rehearsal'?!rehearsalStatuses.includes(a.status):(!liveEnabled&&!previous.jobs?.[id]?.attempts?.some((old:any)=>old.id===a.id&&old.mode==='supervised_pilot'))||a.mode!=='supervised_pilot'||!['prepared','open_pending','opened','fill_pending','filled','upload_pending','uploaded','form_reviewed','submit_pending','submitted','confirmed','uncertain','reconciled_held'].includes(a.status)))throw Error('Live submission is disabled');
 }
 for(const [id,old] of Object.entries(previous.jobs||{}) as [string,any][]){
  const job=next.jobs[id];if(!job)throw Error('Existing jobs cannot be removed');
  if(JSON.stringify(job.tracking)!==JSON.stringify(old.tracking))throw Error('Use the tracking API for tracking changes');
  if(job.attempts.length<old.attempts.length||old.attempts.some((a:any,i:number)=>job.attempts[i]?.id!==a.id))throw Error('Attempt history cannot be replaced');
  for(let i=0;i<old.attempts.length;i++){const a=old.attempts[i],b=job.attempts[i];if(a.mode!==b.mode)throw Error('Attempt mode is immutable');if(['supervised_pilot','presubmit_rehearsal'].includes(a.mode)){if(a.mode==='supervised_pilot'&&!liveEnabled&&a.status!==b.status&&!['confirmed','uncertain','reconciled_held'].includes(b.status))throw Error('Live execution disabled');for(const key of ['grant','answers','answers_sha256','pdf_sha256','pdf_path','candidate_hash','document_id','owner_token','package','submission_context'])if(JSON.stringify(a[key])!==JSON.stringify(b[key]))throw Error('Approved attempt binding is immutable');const transitions:Record<string,string[]>={prepared:['open_pending'],open_pending:['opened'],opened:['fill_pending'],fill_pending:['filled'],filled:['fill_pending','upload_pending'],upload_pending:['uploaded'],uploaded:['form_reviewed'],form_reviewed:['submit_pending'],submit_pending:['submitted','confirmed'],submitted:['confirmed'],uncertain:['reconciled_held','confirmed'],confirmed:[],reconciled_held:[]};if(a.mode==='presubmit_rehearsal'){if(b.status!=='reconciled_held'&&previous.browser?.attempt_id===a.id&&stable(next.browser)!==stable(previous.browser))throw Error('Rehearsal retains browser ownership');transitions.form_reviewed=[];transitions.uncertain=['reconciled_held'];if(JSON.stringify(a.review_evidence)!==JSON.stringify(b.review_evidence)&&a.review_evidence)throw Error('Rehearsal review evidence is immutable');if(a.review_sha256&&a.review_sha256!==b.review_sha256)throw Error('Rehearsal review hash is immutable');if(JSON.stringify(job.funnel_events||[])!==JSON.stringify(old.funnel_events||[]))throw Error('Rehearsal cannot create application events');}if(a.status!==b.status&&!((transitions[a.status]||[]).includes(b.status)||(!['confirmed','reconciled_held'].includes(a.status)&&b.status==='uncertain')))throw Error('Invalid pilot transition');}}
  if(job.snapshots.length<old.snapshots.length||old.snapshots.some((a:any,i:number)=>JSON.stringify(job.snapshots[i])!==JSON.stringify(a)))throw Error('Snapshots are immutable');
 }
 for(const [key,id] of Object.entries(previous.aliases||{}))if(next.aliases[key]!==id)throw Error('Source identity cannot be reassigned');
}
