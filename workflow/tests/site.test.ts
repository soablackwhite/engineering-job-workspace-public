import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {emptyWorkflow,validateState} from '../../lib/workflow';
test('additive migration preserves durable tracking and CAS rejects stale writes',()=>{
 const db=new DatabaseSync(':memory:');
 db.exec(readFileSync('drizzle/0000_narrow_firebird.sql','utf8'));
 db.prepare('INSERT INTO tracking VALUES(?,?,?,?,?)').run('job-001','Applied','Keep existing notes','2026-09-20','2026-09-13');
 db.exec(readFileSync('drizzle/0001_minor_cable.sql','utf8'));
 const sql='INSERT INTO workflow_state(id,revision,payload) VALUES(1,1,?) ON CONFLICT(id) DO UPDATE SET revision=workflow_state.revision+1,payload=excluded.payload WHERE workflow_state.revision=?';
 assert.equal(db.prepare(sql).run(JSON.stringify(emptyWorkflow()),0).changes,1);
 assert.equal(db.prepare(sql).run('{}',0).changes,0);
 assert.equal(db.prepare('SELECT notes FROM tracking').get()?.notes,'Keep existing notes');
});
test('server rejects live mode',()=>assert.throws(()=>validateState({...emptyWorkflow(),mode:'live'},emptyWorkflow())));
test('server refuses removal of prior jobs',()=>assert.throws(()=>validateState(emptyWorkflow(),{jobs:{stable:{}}})));
function pilotState(status:string){const s:any=emptyWorkflow();s.mode='supervised_pilot';s.jobs.j={id:'j',attempts:[{id:'a',mode:'supervised_pilot',status,grant:{},answers:{},answers_sha256:'x',pdf_sha256:'y',document_id:'d',owner_token:'t'}],snapshots:[],documents:[],revisions:0,tracking:{}};return s;}
test('server live gate rejects new pilot attempts',()=>assert.throws(()=>validateState(pilotState('prepared'),emptyWorkflow(),false)));
test('server refuses skipped confirmation',()=>assert.throws(()=>validateState(pilotState('confirmed'),pilotState('prepared'),true)));
test('revoked live gate blocks submit but allows read-only confirmation',()=>{
 assert.throws(()=>validateState(pilotState('submit_pending'),pilotState('form_reviewed'),false));
 assert.doesNotThrow(()=>validateState(pilotState('confirmed'),pilotState('submitted'),false));
});
test('schema v2 migration preserves old attempts; downgrade forbidden',()=>{
 const old:any=emptyWorkflow();old.schema_version=1;
 const next:any=JSON.parse(JSON.stringify(old));next.schema_version=2;
 assert.doesNotThrow(()=>validateState(next,old));
 assert.throws(()=>validateState(old,next));
});
test('new supervised package needs versioned evidence; artifacts immutable',()=>{
 assert.throws(()=>validateState(pilotState('prepared'),emptyWorkflow(),true));
 const old:any=emptyWorkflow();old.artifacts={hash:'exact-bytes'};
 assert.throws(()=>validateState({...old,artifacts:{hash:'different'}},old));
});
function rehearsalState(status='prepared'){
 const s:any=pilotState(status);s.mode='presubmit_rehearsal';const a=s.jobs.j.attempts[0];
 a.mode='presubmit_rehearsal';a.candidate_hash='candidate';a.answers={name:'Synthetic'};a.answers_sha256='answers';a.pdf_sha256='pdf';
 a.grant={allow_submit:false,job_id:'j',document_id:'d',snapshot_hash:'snapshot',pdf_sha256:'pdf',fields:{name:{value:'Synthetic'}},answers_sha256:'answers',package_sha256:'package',package_version:'evidence-gated-2'};
 const contract={...a.grant};delete contract.package_sha256;delete contract.package_version;
 a.package={sha256:'package',version:'evidence-gated-2',payload:{job_id:'j',snapshot_hash:'snapshot',candidate_hash:'candidate',answers:{name:'Synthetic'},contract}};
 a.submission_context={artifact_sha256:'pdf'};s.jobs.j.screen={policy_version:'evidence-gated-2',decision:'eligible'};
 s.browser={job_id:'j',attempt_id:'a',token:'t'};
 if(status==='form_reviewed'){a.review_sha256='review';a.review_evidence={mode:a.mode,package_sha256:'package',pdf_sha256:'pdf',answers:a.answers};}
 return s;
}
test('server accepts rehearsal preparation with live submission gate off',()=>assert.doesNotThrow(()=>validateState(rehearsalState(),emptyWorkflow(),false)));
test('rehearsal cannot submit even with live gate enabled',()=>{
 for(const status of ['submit_pending','submitted','confirmed','dry_run_complete'])assert.throws(()=>validateState(rehearsalState(status),rehearsalState('form_reviewed'),true));
 const next=rehearsalState('form_reviewed');next.jobs.j.attempts[0].mode='supervised_pilot';assert.throws(()=>validateState(next,rehearsalState('form_reviewed'),true));
});
test('rehearsal stage transition and immutable bindings',()=>{
 assert.doesNotThrow(()=>validateState(rehearsalState('open_pending'),rehearsalState(),false));
 assert.throws(()=>validateState(rehearsalState('uploaded'),rehearsalState(),false));
 for(const key of ['grant','answers','answers_sha256','package','submission_context','pdf_sha256','candidate_hash']){
  const next=rehearsalState();next.jobs.j.attempts[0][key]='modified';assert.throws(()=>validateState(next,rehearsalState(),false));
 }
 const next=rehearsalState('form_reviewed');next.jobs.j.attempts[0].review_sha256='changed';assert.throws(()=>validateState(next,rehearsalState('form_reviewed'),false));
});
test('rehearsal cannot release uncertain ownership or create application events',()=>{
 const old=rehearsalState('uncertain'),next=rehearsalState('uncertain');next.browser=null;assert.throws(()=>validateState(next,old,false));
 const submitted=rehearsalState('form_reviewed');submitted.jobs.j.funnel_events=[{id:'a',stage:'submitted'}];assert.throws(()=>validateState(submitted,rehearsalState('form_reviewed'),true));
 const extra=rehearsalState();extra.jobs.j.attempts.push({...extra.jobs.j.attempts[0],id:'new'});assert.throws(()=>validateState(extra,rehearsalState(),true));
});
test('rehearsal must begin prepared and exclude submit permission',()=>{
 assert.throws(()=>validateState(rehearsalState('opened'),emptyWorkflow(),false));
 const next=rehearsalState();next.jobs.j.attempts[0].grant.allow_submit=true;assert.throws(()=>validateState(next,emptyWorkflow(),true));
});
