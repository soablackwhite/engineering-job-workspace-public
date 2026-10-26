/** Load only in an existing CUA REPL. No browser acquisition or network clients.
 * processOne(tab,{queuePath,session,mapping,uploadDocsLoaded:true})
 * mapping={url,steps:[{step_id,marker_selector,fields:{answerKey:{selector,
 * label,label_selector?,kind,required}},controls:[{selector,label}],
 * upload?:{input_selector,control_selector,control_label}}],review:{step_id,answers:{key:
 * {selector,kind:'text'|'checkbox'|'boolean_text',true_text?,false_text?}},upload_selector}}
 * Optional upload.stored_resume / review.stored_resume: {job_identity:{selector,text},
 * selected:{selector,attribute:'checked'|'aria-checked'|'aria-selected'},
 * filename:{selector,text},container_selector:'OBSERVED_SELECTED_RESUME_CARD',download:{selector,label},
 * download_mode?:'media_only'|'click_event'|'observed_directory_delta',download_directory?:'CONFIRMED_DOWNLOADS_DIRECTORY'}. Requires downloadDocsLoaded:true.
 * Fresh DOM File bytes or job-bound selected-server PDF download provide byte proof.
 * Cached earlier answers never stand in for the fresh visible final summary.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import {userInfo} from 'node:os';
import {randomBytes,createHash} from 'node:crypto';
const METHODS=new Set(['open','url','label','fill','value','upload','uploaded','file_hash','required_fields','click','snapshot','observe_step','review_summary']);
const EFFECTS=new Set(['fill','upload','click']);
const fail=message=>{throw new Error(message)};
async function privateDir(p){const s=await fs.lstat(p);if(!s.isDirectory()||s.isSymbolicLink()||(s.mode&0o077)||s.uid!==userInfo().uid)fail('Private owner-only queue required')}
async function atomic(p,body){const tmp=p+'.'+randomBytes(16).toString('hex');try{await fs.writeFile(tmp,JSON.stringify(body),{mode:0o600,flag:'wx'});await fs.rename(tmp,p)}finally{await fs.unlink(tmp).catch(()=>{})}}
function stable(value){return JSON.stringify(value,(_key,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v)}
function extendMapping(previous,next){if(previous.url!==next.url||next.steps.length<previous.steps.length||previous.steps.some((step,i)=>stable(step)!==stable(next.steps[i]))||(previous.review&&stable(previous.review)!==stable(next.review)))fail('Observed mapping changed during session');}
async function exists(p){try{await fs.lstat(p);return true}catch(e){if(e.code==='ENOENT')return false;throw e}}
function validateMapping(mapping){
 if(!mapping||typeof mapping.url!=='string'||!Array.isArray(mapping.steps)||!mapping.steps.length)fail('Explicit observed mapping required');
 const ids=new Set(),keys=new Set();
 for(const step of mapping.steps){
  if(!step.step_id||ids.has(step.step_id)||!step.marker_selector||!step.fields||!Array.isArray(step.controls))fail('Invalid explicit step mapping');ids.add(step.step_id);
  for(const [key,f] of Object.entries(step.fields)){if(keys.has(key)||!f.selector||!f.label||!['text','select','checkbox'].includes(f.kind)||typeof f.required!=='boolean')fail('Invalid observed field mapping');keys.add(key)}
  for(const c of step.controls){if(!c.selector||!['Next','Review'].includes(c.label))fail('Only exact Next/Review controls may be bound')}
  if(step.upload){if(step.upload.stored_resume){if(!step.upload.input_selector||step.upload.input_selector!==step.upload.stored_resume.selected?.selector)fail('Stored resume binding must be the observed selected control');validateStoredProof(step.upload.stored_resume)}else if(!step.upload.input_selector||!step.upload.control_selector||!step.upload.control_label)fail('Observed chooser control and DOM file input required')}
 }
 if(mapping.review?.stored_resume){validateStoredProof(mapping.review.stored_resume);if(mapping.review.upload_selector!==mapping.review.stored_resume.selected.selector)fail('Final stored resume binding must be the observed selected control')}
 if(mapping.review){if(!ids.has(mapping.review.step_id)||!mapping.review.answers||!mapping.review.upload_selector||Object.keys(mapping.review.answers).sort().join('\0')!==[...keys].sort().join('\0'))fail('Final summary must map every answer and attachment')}
}
function validateStoredProof(proof){
 if(typeof proof.container_selector!=='string'||!proof.container_selector)fail('Observed selected-resume container required');
 if(proof.download_mode!==undefined&&!['media_only','click_event','observed_directory_delta'].includes(proof.download_mode))fail('Unsupported documented resume download mode');
 for(const key of ['job_identity','filename','download'])if(!proof[key]||typeof proof[key].selector!=='string'||!proof[key].selector||typeof (key==='download'?proof[key].label:proof[key].text)!=='string'||!(key==='download'?proof[key].label:proof[key].text))fail('Explicit observed stored-resume identity/name/download mapping required');
 if(!proof.selected?.selector||!['checked','aria-checked','aria-selected'].includes(proof.selected.attribute))fail('Explicit selected-state control required');
 if(proof.download_mode==='observed_directory_delta'&&proof.download_directory!==path.join(userInfo().homedir,'Downloads'))fail('Observed download directory must be the operator-confirmed user Downloads directory');
 if(/submit|send|apply/i.test(proof.download.label))fail('Unsafe stored-resume download label');
}
function unchangedAttachment(file,prior){if(file.name!==prior.name||file.size!==prior.size||file.sha256!==prior.sha256)fail('Fresh attachment bytes differ from approved prior evidence')}
async function originalPdf(local){
 if(typeof local!=='string'||!path.isAbsolute(local))fail('Absolute reviewed original PDF required');
 const stat=await fs.lstat(local);if(!stat.isFile()||stat.isSymbolicLink()||stat.uid!==userInfo().uid)fail('Approved original must be an owned regular file');
 const bytes=await fs.readFile(local);if(!bytes.subarray(0,5).equals(Buffer.from('%PDF-')))fail('Approved original must contain PDF bytes');
 return {name:path.basename(local),size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
}
async function storedBinding(tab,proof,requireDownload=true){
 await dom(tab,'stored_relation',{container_selector:proof.container_selector,selectors:[proof.selected.selector,proof.filename.selector,...(requireDownload?[proof.download.selector]:[])]});
 const identity=await dom(tab,'summary',{selector:proof.job_identity.selector,kind:'text'});if(identity!==proof.job_identity.text)fail('Job-specific identity marker changed');
 await dom(tab,'selected',proof.selected);
 const name=await dom(tab,'summary',{selector:proof.filename.selector,kind:'text'});if(name!==proof.filename.text)fail('Visible selected resume filename changed');
 if(requireDownload){const download=await dom(tab,'control',proof.download);if(download.label!==proof.download.label||download.disabled)fail('Observed resume download control changed')}
 return name;
}
function matchingDownloadName(actual,visible){
 if(actual===visible)return true;const extension=path.extname(visible),stem=visible.slice(0,visible.length-extension.length),prefix=stem+' (',suffix=')'+extension;
 if(!actual.startsWith(prefix)||!actual.endsWith(suffix))return false;return /^[1-9]\d*$/.test(actual.slice(prefix.length,actual.length-suffix.length));
}
export async function snapshotDownloadDirectory(directory,visible){
 if(!visible||path.basename(visible)!==visible||path.extname(visible).toLowerCase()!=='.pdf')fail('Exact visible PDF filename required');
 const dir=await fs.lstat(directory);if(!dir.isDirectory()||dir.isSymbolicLink()||dir.uid!==userInfo().uid)fail('Download directory must be an owned real directory');
 const result=[];for(const name of (await fs.readdir(directory)).filter(name=>matchingDownloadName(name,visible))){const local=path.join(directory,name);const st=await fs.lstat(local);if(!st.isFile()||st.isSymbolicLink()||st.uid!==userInfo().uid)fail('Matching download must be an owned regular file');result.push({path:local,name,ino:st.ino,dev:st.dev,size:st.size,mtimeMs:st.mtimeMs,ctimeMs:st.ctimeMs,birthtimeMs:st.birthtimeMs})}return result;
}
export function newDownloadCandidates(before,after,clickedAt){
 const baseline=new Map(before.map(file=>[file.path,file]));return after.filter(file=>{const old=baseline.get(file.path);const changed=!old||['ino','dev','size','mtimeMs','ctimeMs'].some(key=>old[key]!==file[key]);return changed&&file.size>0&&file.mtimeMs+1>=clickedAt&&file.ctimeMs+1>=clickedAt});
}
async function privateDownloadCopy(candidate,queuePath,operation,expected){
 await privateDir(queuePath);const st=await fs.lstat(candidate.path);if(!st.isFile()||st.isSymbolicLink()||st.uid!==userInfo().uid||['ino','dev','size','mtimeMs','ctimeMs'].some(key=>st[key]!==candidate[key]))fail('Observed downloaded file changed before byte read');
 const bytes=await fs.readFile(candidate.path);const after=await fs.lstat(candidate.path);if(['ino','dev','size','mtimeMs','ctimeMs'].some(key=>after[key]!==candidate[key]))fail('Observed downloaded file changed during byte read');
 if(!bytes.subarray(0,5).equals(Buffer.from('%PDF-')))fail('Observed download is not PDF bytes');const sha256=createHash('sha256').update(bytes).digest('hex');if(!expected||sha256!==expected.sha256||bytes.length!==expected.size)fail('Observed downloaded bytes differ from approved original');
 const artifacts=path.join(queuePath,'download-artifacts');await fs.mkdir(artifacts,{mode:0o700}).catch(e=>{if(e.code!=='EEXIST')throw e});await privateDir(artifacts);
 const local=path.join(artifacts,operation+'-'+randomBytes(12).toString('hex')+'.pdf');await fs.writeFile(local,bytes,{mode:0o600,flag:'wx'});
 const copied=await fs.readFile(local);if(createHash('sha256').update(copied).digest('hex')!==sha256)fail('Private downloaded copy byte check failed');
 return {name:expected.name,size:bytes.length,sha256,bytes_path:local,actual_download_path:candidate.path,actual_download_metadata:candidate,source:'observed_directory_delta',download_operation:operation};
}
export async function waitForDownloadDelta(directory,visible,before,clickedAt,deadline,check){
 let candidate=null,stableSince=0;
 while(Date.now()<deadline){await check();const found=newDownloadCandidates(before,await snapshotDownloadDirectory(directory,visible),clickedAt);if(found.length>1)fail('Multiple new matching downloads; ambiguous artifact');if(found.length===1){if(candidate&&stable(candidate)===stable(found[0])){if(Date.now()-stableSince>=100)return found[0]}else{candidate=found[0];stableSince=Date.now()}}else{candidate=null;stableSince=0}await new Promise(resolve=>setTimeout(resolve,Math.min(50,Math.max(1,deadline-Date.now()))))}
 fail('No single completed new matching download before deadline; no retry');
}
export async function reconcileObservedDownload({directory,visibleFilename,before,clickedAt,deadline,queuePath,operation,expected,check=async()=>{}}){
 if(!Array.isArray(before)||!Number.isFinite(clickedAt)||!Number.isFinite(deadline)||deadline<=clickedAt||deadline-clickedAt>10000||!expected||expected.name!==visibleFilename||typeof expected.sha256!=='string'||!/^[a-f0-9]{64}$/.test(expected.sha256)||!Number.isSafeInteger(expected.size)||expected.size<=0||typeof operation!=='string'||!/^[a-f0-9]{32,128}$/.test(operation))fail('Bound snapshot, finite click window, operation and approved file tuple required');
 const candidate=await waitForDownloadDelta(directory,visibleFilename,before,clickedAt,deadline,check);await check();if(Date.now()>=deadline)fail('Observed download arrived after deadline');
 const file=await privateDownloadCopy(candidate,queuePath,operation,expected);await check();if(Date.now()>=deadline)fail('Observed downloaded-byte proof completed after deadline');
 const latest=newDownloadCandidates(before,await snapshotDownloadDirectory(directory,visibleFilename),clickedAt);if(latest.length!==1||stable(latest[0])!==stable(candidate))fail('Observed download delta changed before proof completion');
 return {...file,proof_scope:'download_artifact_only',clicked_at_ms:clickedAt,deadline_ms:deadline};
}
async function directoryDelta(tab,proof,opts,req,check,expected){
 if(opts.directoryDownloadsConfirmed!==true)fail('Observed directory reconciliation requires explicit operator confirmation');
 const before=await snapshotDownloadDirectory(proof.download_directory,proof.filename.text);await storedBinding(tab,proof);await check();const clickedAt=Date.now(),deadline=Math.min(req.deadline*1000,clickedAt+10000);req.effect_started=true;
 await tab.playwright.locator(proof.download.selector).click();
 const freshCheck=async()=>{await check();await storedBinding(tab,proof,false)};
 return reconcileObservedDownload({directory:proof.download_directory,visibleFilename:proof.filename.text,before,clickedAt,deadline,queuePath:opts.queuePath,operation:req.operation,expected,check:freshCheck});
}
async function storedPdf(tab,proof,opts,req,check,expected){
 if(opts.downloadDocsLoaded!==true)fail('Root must read documented downloadMedia API before stored-resume reuse');
 const name=await storedBinding(tab,proof);await check();
 if(proof.download_mode==='observed_directory_delta')return directoryDelta(tab,proof,opts,req,check,expected);
 const remaining=Math.floor((req.deadline-Date.now()/1000)*1000);if(remaining<=0)fail('Stored-resume download deadline expired');
 let local;
 if(proof.download_mode==='click_event'){
  const timeoutMs=Math.min(10000,remaining);
  // Register the documented download event BEFORE the one explicit Download click.
  const pending=tab.playwright.waitForEvent('download',{timeoutMs});pending.catch(()=>{});
  try{await storedBinding(tab,proof);await check();req.effect_started=true;await tab.playwright.locator(proof.download.selector).click();const download=await pending;await check();const pathTimeout=Math.floor((req.deadline-Date.now()/1000)*1000);if(pathTimeout<=0)fail('Download path deadline expired');local=await download.path({timeoutMs:Math.min(10000,pathTimeout)})}
  catch(error){await pending.catch(()=>{});throw error}
 }else local=await tab.playwright.locator(proof.download.selector).downloadMedia({timeoutMs:Math.min(10000,remaining)});
 await check();await storedBinding(tab,proof,false);
 if(typeof local!=='string'||!path.isAbsolute(local))fail('Documented downloadMedia did not return an actual local PDF path');
 const stat=await fs.lstat(local);if(!stat.isFile()||stat.isSymbolicLink()||stat.uid!==userInfo().uid||(stat.mode&0o077))fail('Downloaded PDF must be a private owned regular file');
 const bytes=await fs.readFile(local);if(!bytes.subarray(0,5).equals(Buffer.from('%PDF-')))fail('Resume download did not contain PDF bytes');
 await check();await storedBinding(tab,proof,false);
 return {name,size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),bytes_path:local,source:'selected_server_resume_download'};
}
async function dom(tab,action,arg={}){
 return tab.playwright.evaluate(async ({action,arg})=>{
  const visible=e=>!!e&&!!(e.getClientRects().length)&&getComputedStyle(e).visibility!=='hidden';
  const one=s=>{const a=[...document.querySelectorAll(s)].filter(visible);if(a.length!==1)throw Error('Expected exactly one visible mapped element');return a[0]};
  const field=f=>{const e=one(f.selector);const label=f.label_selector?one(f.label_selector).textContent.trim():[...(e.labels||[])].map(x=>x.textContent.trim()).join(' ');const kind=e.tagName==='SELECT'?'select':e.tagName==='TEXTAREA'?'text':e.tagName==='INPUT'?(e.type==='checkbox'?'checkbox':e.type==='file'?'file':['hidden','button','submit','radio'].includes(e.type)?'unsupported':'text'):'unsupported';return {e,label,kind}};
  if(action==='present'){return [...document.querySelectorAll(arg.selector)].filter(visible).length===1}
  if(action==='field'){const {e,label,kind}=field(arg);return {selector:arg.selector,label,kind,required:e.required===true||e.getAttribute('aria-required')==='true',value:kind==='checkbox'?e.checked:e.value}}
  if(action==='control'){const e=one(arg.selector);return {selector:arg.selector,label:(e.innerText||e.textContent||'').trim(),disabled:e.disabled===true||e.getAttribute('aria-disabled')==='true'}}
  if(action==='required'){return [...document.querySelectorAll('input,select,textarea,[aria-required="true"]')].filter(e=>visible(e)&&e.type!=='hidden'&&!e.disabled&&(e.required||e.getAttribute('aria-required')==='true')).map(e=>{const bound=(arg.selectors||[]).filter(s=>{const matches=[...document.querySelectorAll(s)].filter(visible);return matches.length===1&&matches[0]===e});return bound.length===1?bound[0]:e.id?'#'+CSS.escape(e.id):'UNBOUND_REQUIRED_FIELD'})}
  if(action==='file'){
   // File inputs may be hidden behind the explicitly observed upload button.
   const all=[...document.querySelectorAll(arg.selector)];if(all.length!==1||all[0].type!=='file'||all[0].files?.length!==1)throw Error('Exactly one DOM File required');
   const f=all[0].files[0];const bytes=await f.arrayBuffer();const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(x=>x.toString(16).padStart(2,'0')).join('');return {name:f.name,size:f.size,sha256:hash};
  }
  if(action==='stored_relation'){
   const container=one(arg.container_selector);
   for(const selector of arg.selectors){const element=one(selector);if(!container.contains(element))throw Error('Resume selection/name/download are not in the same observed card')}
   return true;
  }
  if(action==='selected'){
   const e=one(arg.selector);let selected=false;
   if(arg.attribute==='checked'){if(e.tagName!=='INPUT'||!['radio','checkbox'].includes(e.type))throw Error('Selected resume control type differs');selected=e.checked===true}
   else if(['aria-checked','aria-selected'].includes(arg.attribute))selected=e.getAttribute(arg.attribute)==='true';
   else throw Error('Unsupported explicit selected-state attribute');
   if(!selected)throw Error('Stored resume is not currently selected');return true;
  }
  if(action==='summary'){
   const e=one(arg.selector);
   if(arg.kind==='checkbox'){if(e.tagName!=='INPUT'||e.type!=='checkbox')throw Error('Summary checkbox type differs');return e.checked}
   const text=(e.innerText||e.textContent||'').trim();
   if(arg.kind==='boolean_text'){if(text===arg.true_text)return true;if(text===arg.false_text)return false;throw Error('Summary boolean differs')}
   if(arg.kind!=='text')throw Error('Unsupported summary representation');return text;
  }
  throw Error('Read-only DOM action outside catalog');
 },{action,arg},{timeoutMs:10000});
}
async function boundStep(tab,mapping){const found=[];for(const step of mapping.steps)if(await dom(tab,'present',{selector:step.marker_selector}))found.push(step);if(found.length!==1)fail('Current visible step is not uniquely mapped');return found[0]}
function matchingField(step,f){const match=Object.values(step.fields).find(x=>x.selector===f?.selector);if(!match||f.label!==match.label||f.kind!==match.kind||f.label_selector!==match.label_selector)fail('Field outside exact observed catalog');return match}
async function verifiedField(tab,f){const got=await dom(tab,'field',f);if(got.label!==f.label||got.kind!==f.kind||got.required!==f.required)fail('Live DOM field differs from observed mapping');return got}
async function observe(tab,mapping){const step=await boundStep(tab,mapping);const fields=[];for(const f of Object.values(step.fields)){const x=await verifiedField(tab,f);fields.push({selector:x.selector,label:x.label,kind:x.kind,required:x.required})}const known=new Set(fields.map(f=>f.selector));if(step.upload)known.add(step.upload.input_selector);for(const selector of await dom(tab,'required',{selectors:[...known]}))if(!known.has(selector))fields.push({selector,label:'UNBOUND_REQUIRED_FIELD',kind:'unknown',required:true});const controls=[];for(const c of step.controls){const got=await dom(tab,'control',c);if(got.label!==c.label||got.disabled)fail('Exact safe navigation control changed');controls.push(c)}return {step_id:step.step_id,fields,upload_selector:step.upload?.input_selector||null,controls,snapshot:await tab.playwright.domSnapshot()}}
async function safeObservation(tab,mapping){const observation=await observe(tab,mapping);if(observation.fields.some(f=>f.kind==='unknown'))fail('Unbound required field; stop before effect');return observation}
async function execute(tab,req,opts,mapping,state,check){
 const url=await tab.url();if(url!==mapping.url)fail('Tab URL differs from bound mapping');
 if(req.method==='open'){if(req.args[0]!==mapping.url)fail('Open outside bound URL');return {url,already_bound:true}}
 if(req.method==='url')return url;
 if(req.method==='snapshot')return tab.playwright.domSnapshot();
 if(req.method==='required_fields'){const current=await boundStep(tab,mapping);return dom(tab,'required',{selectors:[...Object.values(current.fields).map(f=>f.selector),...(current.upload?[current.upload.input_selector]:[])]})}
 if(req.method==='observe_step')return observe(tab,mapping);
 const step=await boundStep(tab,mapping);
 if(['label','value','fill'].includes(req.method)){
  const f=matchingField(step,req.args[0]);const observed=await verifiedField(tab,f);
  if(req.method==='label')return observed.label;
  if(req.method==='value')return observed.value;
  const value=req.args[1];if(f.kind==='checkbox'?typeof value!=='boolean':typeof value!=='string')fail('Typed fill value differs');
  await safeObservation(tab,mapping);await check();const locator=tab.playwright.locator(f.selector);
  if(f.kind==='checkbox')await locator.setChecked(value);else if(f.kind==='select')await locator.selectOption(value);else await locator.fill(value);
  const fresh=await verifiedField(tab,f);if(fresh.value!==value)fail('Fill readback differs');state.readbacks[f.selector]={value,step_id:step.step_id,operation:req.operation};return {verified:true};
 }
 if(['upload','uploaded','file_hash'].includes(req.method)){
  const selector=req.args[0];const final=step.step_id===mapping.review?.step_id;
  if(selector!==(final?mapping.review.upload_selector:step.upload?.input_selector))fail('Attachment outside observed step catalog');
  const stored=final?mapping.review.stored_resume:step.upload?.stored_resume;
  if(stored){
   if(req.method==='upload'){
    if(final)fail('Cannot establish attachment at final review');
    await safeObservation(tab,mapping);await check();
    const original=await originalPdf(req.args[1]);
    if(original.name!==stored.filename.text)fail('Selected stored resume filename differs from approved original');
    const file=await storedPdf(tab,stored,opts,req,check,original);
    if(path.resolve(file.bytes_path)===path.resolve(req.args[1]))fail('Server download path cannot be the original reference path');
    if(file.sha256!==original.sha256||file.size!==original.size)fail('Selected server resume bytes differ from approved original');
    state.attachment={...file,download_source:file.source,selector,step_id:step.step_id,operation:req.operation,reference_path:req.args[1],job_identity:stored.job_identity.text,source:'selected_server_resume_download'};
    return {verified:true,reused_stored_resume:true,...file};
   }
   if(!state.attachment||state.attachment.source!=='selected_server_resume_download')fail('Stored resume has no bound verified reuse');
   if(stored.job_identity.text!==state.attachment.job_identity)fail('Stored resume job identity differs from prior binding');
   const file=await storedPdf(tab,stored,opts,req,check,state.attachment);
   if(path.resolve(file.bytes_path)===path.resolve(state.attachment.reference_path))fail('Fresh download cannot fall back to the original reference path');
   unchangedAttachment(file,state.attachment);state.attachment_readbacks=[...(state.attachment_readbacks||[]),{...file,operation:req.operation,step_id:step.step_id}];
   return req.method==='uploaded'?file.name:file.sha256;
  }
  if(state.attachment?.source==='selected_server_resume_download')fail('Stored resume mode cannot switch to a local File');
  if(req.method==='upload'){
   if(opts.uploadDocsLoaded!==true)fail('Root must read documented file chooser API before upload');
   const local=req.args[1];if(typeof local!=='string'||!path.isAbsolute(local))fail('Absolute reviewed attachment path required');
   const control=await dom(tab,'control',{selector:step.upload.control_selector});if(control.disabled||control.label!==step.upload.control_label||/submit|send|apply/i.test(control.label))fail('Unsafe upload control');
   await safeObservation(tab,mapping);await check();
   const chooserPromise=tab.playwright.waitForEvent('filechooser',{timeoutMs:Math.max(1,Math.min(10000,(req.deadline-Date.now()/1000)*1000))});chooserPromise.catch(()=>{});
   try{
    await tab.playwright.locator(step.upload.control_selector).click();const chooser=await chooserPromise;await check();
    const remaining=Math.floor((req.deadline-Date.now()/1000)*1000);if(remaining<=0)fail('Attachment upload deadline expired');
    await chooser.setFiles(local,{timeoutMs:Math.min(10000,remaining)});
   }catch(error){await chooserPromise.catch(()=>{});throw error}
  }
  const file=await dom(tab,'file',{selector});if(req.method==='upload')state.attachment={...file,selector,step_id:step.step_id,operation:req.operation};else if(state.attachment&&(file.sha256!==state.attachment.sha256||file.name!==state.attachment.name||file.size!==state.attachment.size))fail('Attachment changed since verified upload');return req.method==='uploaded'?file.name:req.method==='file_hash'?file.sha256:{verified:true,...file};
 }
 if(req.method==='click'){
  const c=step.controls.find(c=>c.selector===req.args[0]);if(!c||!['Next','Review'].includes(c.label))fail('Only exact cataloged Next/Review clicks allowed');
  await safeObservation(tab,mapping);const live=await dom(tab,'control',c);if(live.label!==c.label||live.disabled)fail('Safe control changed');await check();await tab.playwright.locator(c.selector).click();return {navigation_only:true};
 }
 if(req.method==='review_summary'){
  const review=mapping.review;if(!review||review.step_id!==step.step_id)fail('Not observed final review page');
  const answers={};for(const [key,entry]of Object.entries(review.answers))answers[key]=await dom(tab,'summary',entry);
  if(!state.attachment)fail('No previously byte-verified attachment');
  if(review.stored_resume&&review.stored_resume.job_identity.text!==state.attachment.job_identity)fail('Final resume job identity differs from prior binding');
  const file=review.stored_resume?await storedPdf(tab,review.stored_resume,opts,req,check,state.attachment):await dom(tab,'file',{selector:review.upload_selector});
  if(review.stored_resume&&state.attachment.source!=='selected_server_resume_download')fail('Final stored resume lacks prior bound stored-file proof');
  if(!review.stored_resume&&state.attachment.source==='selected_server_resume_download')fail('Final stored resume cannot switch to DOM File mode');
  if(review.stored_resume&&path.resolve(file.bytes_path)===path.resolve(state.attachment.reference_path))fail('Final download cannot fall back to the original reference path');
  unchangedAttachment(file,state.attachment);
  return {answers,pdf_name:file.name,pdf_sha256:file.sha256,attachment_evidence:{source:review.stored_resume?'fresh_final_selected_server_resume_download':'fresh_final_DOM_File_bytes',size:file.size,bytes_path:file.bytes_path||null,reference_path:state.attachment.reference_path||null,actual_download_path:file.actual_download_path||null,actual_download_metadata:file.actual_download_metadata||null,download_operation:file.download_operation||null,download_source:file.source||null,previous_operation:state.attachment.operation,job_identity:review.stored_resume?.job_identity||null},snapshot:await tab.playwright.domSnapshot()};
 }
 fail('Unsupported operation');
}
export async function processOne(tab,options){
 const opts={...options};const mapping=JSON.parse(JSON.stringify(opts.mapping));validateMapping(mapping);
 if(!/^[a-f0-9]{32,128}$/.test(opts.session)||!path.isAbsolute(opts.queuePath))fail('Private queue/session binding required');
 const root=opts.queuePath;for(const name of ['','requests','responses','claims'])await privateDir(path.join(root,name));
 const blocked=path.join(root,'blocked-'+opts.session+'.json');if(await exists(blocked))return {status:'blocked'};
 const names=(await fs.readdir(path.join(root,'requests'))).filter(x=>/^[a-f0-9]{48}\.json$/.test(x)).sort();
 for(const name of names){
  const requestPath=path.join(root,'requests',name);const stat=await fs.lstat(requestPath);if(!stat.isFile()||stat.isSymbolicLink()||(stat.mode&0o077))fail('Untrusted request artifact');
  const req=JSON.parse(await fs.readFile(requestPath,'utf8'));if(req.session!==opts.session)continue;
  if(req.version!==1||req.operation+'.json'!==name||!METHODS.has(req.method)||!Array.isArray(req.args)||!Number.isFinite(req.deadline))fail('Invalid closed RPC request');
  const claim=path.join(root,'claims',name);try{const fd=await fs.open(claim,'wx',0o600);await fd.close()}catch(e){if(e.code==='EEXIST')continue;throw e}
  // Claim remains durable on every failure, including crash; never repeat effects.
  const check=async()=>{if(Date.now()/1000>=req.deadline||await exists(blocked))fail('Request expired or session blocked; never replay')};
  const mappingHash=createHash('sha256').update(JSON.stringify(mapping)).digest('hex');const statePath=path.join(root,'state-'+opts.session+'.json');let state={mapping_sha256:mappingHash,readbacks:{},attachment:null};if(await exists(statePath)){const st=await fs.lstat(statePath);if(st.isSymbolicLink()||(st.mode&0o077))fail('Untrusted state artifact');state=JSON.parse(await fs.readFile(statePath,'utf8'));if(state.mapping_catalog)extendMapping(state.mapping_catalog,mapping);else if(state.mapping_sha256!==mappingHash)fail('Observed mapping changed during session; establish legacy catalog before extension')}
  const priorHash=state.mapping_sha256;
  if(priorHash!==mappingHash){state.mapping_chain=[...(state.mapping_chain||[]),{previous_sha256:priorHash,sha256:mappingHash,version:1}];}
  state.mapping_catalog=JSON.parse(JSON.stringify(mapping));state.mapping_sha256=mappingHash;
  let response={version:1,session:req.session,operation:req.operation,deadline:req.deadline};
  try{await check();response.result=await execute(tab,req,opts,mapping,state,check);await check();await atomic(statePath,state);response.ok=true}
  catch(e){response.ok=false;response.error=String(e.message||'Unconfirmed CUA result');if(EFFECTS.has(req.method)||req.effect_started)await atomic(blocked,{operation:req.operation,reason:'Effect unconfirmed; reconcile visible tab before a new session'});}
  response.completed_at=Date.now()/1000;
  if(response.completed_at>=req.deadline){await atomic(blocked,{operation:req.operation,reason:'Late result discarded; do not replay'});return {status:'expired',operation:req.operation}}
  await atomic(path.join(root,'responses',name),response);return {status:response.ok?'completed':'held',operation:req.operation,method:req.method,error:response.error};
 }
 return {status:'idle'};
}
