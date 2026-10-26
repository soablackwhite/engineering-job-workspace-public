import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {emptyWorkflow,validateState} from '../../lib/workflow';
import {readStoredWorkflow,saveStoredWorkflow,applyWorkflowChanges,WorkflowConflict} from '../../lib/workflow-storage-core';
function fixture(){
 const sql=new DatabaseSync(':memory:');sql.exec('CREATE TABLE workflow_state(id INTEGER PRIMARY KEY,revision INTEGER,payload TEXT)');
 const db={prepare(query:string){const statement=sql.prepare(query);return {async first(){return statement.get();},bind(...values:any[]){return {async run(){return {meta:{changes:statement.run(...values).changes}};}};}};}};
 const objects=new Map<string,string>();
 const bucket={async get(key:string){const value=objects.get(key);return value===undefined?null:{size:new TextEncoder().encode(value).length,text:async()=>value};},async put(key:string,value:string){objects.set(key,value);}};
 return {sql,db,bucket,objects};
}
test('legacy inline data migrates losslessly, grows beyond 1.5 MB and retains CAS and history',async()=>{
 const {sql,db,bucket,objects}=fixture();const state:any=emptyWorkflow();state.candidate={evidence:'x'.repeat(1490000),answer:null};
 sql.prepare('INSERT INTO workflow_state VALUES(1,7,?)').run(JSON.stringify(state));
 assert.deepEqual((await readStoredWorkflow(db,bucket,emptyWorkflow)).state,state);
 const next=applyWorkflowChanges(state,[{op:'set',path:['candidate','new_evidence'],value:'y'.repeat(200000)}]);
 assert.equal((await saveStoredWorkflow(db,bucket,7,next,emptyWorkflow,validateState)).revision,8);
 assert.deepEqual(await readStoredWorkflow(db,bucket,emptyWorkflow),{revision:8,state:next});
 assert.ok(String(sql.prepare('SELECT payload FROM workflow_state').get()?.payload).length<500);
 assert.ok([...objects.values()].includes(JSON.stringify(state)));
 await assert.rejects(saveStoredWorkflow(db,bucket,7,state,emptyWorkflow,validateState),WorkflowConflict);
 assert.deepEqual((await readStoredWorkflow(db,bucket,emptyWorkflow)).state,next);
});
test('storage failure leaves the database pointer and existing state untouched',async()=>{
 const {sql,db,bucket}=fixture(),state=emptyWorkflow();sql.prepare('INSERT INTO workflow_state VALUES(1,1,?)').run(JSON.stringify(state));
 const broken={...bucket,put:async()=>{throw Error('Storage unavailable');}};
 await assert.rejects(saveStoredWorkflow(db,broken,1,state,emptyWorkflow,validateState));
 assert.deepEqual(await readStoredWorkflow(db,bucket,emptyWorkflow),{revision:1,state});
});
test('concurrent save cannot replace the winner',async()=>{
 const {db,bucket}=fixture();await saveStoredWorkflow(db,bucket,0,emptyWorkflow(),emptyWorkflow,validateState);
 const a:any=emptyWorkflow(),b:any=emptyWorkflow();a.candidate={name:'a'};b.candidate={name:'b'};
 const results=await Promise.allSettled([saveStoredWorkflow(db,bucket,1,a,emptyWorkflow,validateState),saveStoredWorkflow(db,bucket,1,b,emptyWorkflow,validateState)]);
 assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal(results.filter(r=>r.status==='rejected').length,1);
 assert.equal((await readStoredWorkflow(db,bucket,emptyWorkflow)).revision,2);
});
test('missing or modified R2 data fails closed',async()=>{
 const {db,bucket,objects,sql}=fixture();await saveStoredWorkflow(db,bucket,0,emptyWorkflow(),emptyWorkflow,validateState);
 const key=JSON.parse(String(sql.prepare('SELECT payload FROM workflow_state').get()?.payload)).key;
 objects.set(key,'{}');await assert.rejects(readStoredWorkflow(db,bucket,emptyWorkflow),/integrity/);
 objects.delete(key);await assert.rejects(readStoredWorkflow(db,bucket,emptyWorkflow),/missing/);
});
test('deltas preserve null, arrays and removals but cannot mutate prototypes or bypass immutable history',()=>{
 const before={a:null,b:{x:1},c:[1,2],keep:true};
 const after=applyWorkflowChanges(before,[{op:'set',path:['a'],value:false},{op:'remove',path:['b','x']},{op:'set',path:['c'],value:[]},{op:'set',path:['b','nullable'],value:null}]);
 assert.deepEqual(after,{a:false,b:{nullable:null},c:[],keep:true});assert.deepEqual(before,{a:null,b:{x:1},c:[1,2],keep:true});
 for(const path of [['__proto__','polluted'],['b','constructor'],[],['c','0']])assert.throws(()=>applyWorkflowChanges(before,[{op:'set',path,value:true}]));
 const previous:any=emptyWorkflow();previous.artifacts={hash:'original'};
 const changed=applyWorkflowChanges(previous,[{op:'set',path:['artifacts','hash'],value:'replacement'}]);assert.throws(()=>validateState(changed,previous),/immutable/);
});
