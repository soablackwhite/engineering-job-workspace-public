// D1 owns the atomic revision pointer; private R2 retains complete immutable states.
// The legacy inline row is read without modification and archived on the first write.
export const storageProtocol = 'r2-versioned-delta-v1';
export const maxStateBytes = 8 * 1024 * 1024;
type Database = {prepare(sql:string):any};
type Bucket = {get(key:string):Promise<any>;put(key:string,value:string,options?:any):Promise<any>};
type Pointer = {storage:typeof storageProtocol;key:string;sha256:string;bytes:number;revision:number};
export class WorkflowConflict extends Error {}
export class WorkflowInvalid extends Error {}
const bytes=(text:string)=>new TextEncoder().encode(text).byteLength;
async function digest(text:string){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))).map(x=>x.toString(16).padStart(2,'0')).join('');}
function isPointer(value:any):value is Pointer{return value?.storage===storageProtocol;}
async function archive(bucket:Bucket,state:any,revision:number,previousKey?:string){
 const body=JSON.stringify(state),size=bytes(body);
 if(size>maxStateBytes)throw new WorkflowInvalid('Workflow state exceeds the safe 8 MiB processing bound');
 const sha256=await digest(body),key=`workflow/states/${revision}-${sha256}.json`;
 const existing=await bucket.get(key);
 if(existing){if(await existing.text()!==body)throw Error('Existing workflow archive differs');}
 else await bucket.put(key,body,{httpMetadata:{contentType:'application/json'},customMetadata:{revision:String(revision),...(previousKey?{previous_key:previousKey}:{})}});
 return {storage:storageProtocol,key,sha256,bytes:size,revision} as Pointer;
}
export async function readStoredWorkflow(db:Database,bucket:Bucket,empty:()=>any){
 const row=await db.prepare('SELECT revision,payload FROM workflow_state WHERE id=1').first();
 if(!row)return {revision:0,state:empty()};
 const value=JSON.parse(row.payload);
 if(!isPointer(value))return {revision:row.revision,state:value};
 if(value.revision!==row.revision||value.key!==`workflow/states/${row.revision}-${value.sha256}.json`||!/^[a-f0-9]{64}$/.test(value.sha256)||value.bytes>maxStateBytes)throw Error('Invalid workflow storage pointer');
 const object=await bucket.get(value.key);if(!object)throw Error('Workflow archive missing');
 if(object.size!==undefined&&object.size>maxStateBytes)throw Error('Workflow archive exceeds processing bound');
 const body=await object.text();
 if(bytes(body)!==value.bytes||await digest(body)!==value.sha256)throw Error('Workflow archive integrity check failed');
 return {revision:row.revision,state:JSON.parse(body)};
}
export type WorkflowChange={path:string[];op:'set'|'remove';value?:unknown};
export function applyWorkflowChanges(previous:any,changes:unknown){
 if(!Array.isArray(changes)||changes.length>20000)throw new WorkflowInvalid('Invalid workflow changes');
 const state=JSON.parse(JSON.stringify(previous));
 for(const change of changes){
  if(!change||!Array.isArray(change.path)||change.path.length===0||change.path.length>64||!['set','remove'].includes(change.op)||change.path.some((k:any)=>typeof k!=='string'||!k.length||k.length>1024||['__proto__','prototype','constructor'].includes(k)))throw new WorkflowInvalid('Invalid workflow change path');
  if(change.op==='set'&&!Object.hasOwn(change,'value'))throw new WorkflowInvalid('Workflow set requires a value');
  let parent=state;
  for(const key of change.path.slice(0,-1)){if(!parent||Array.isArray(parent)||typeof parent!=='object'||!Object.hasOwn(parent,key))throw new WorkflowInvalid('Workflow change parent missing');parent=parent[key];}
  if(!parent||Array.isArray(parent)||typeof parent!=='object')throw new WorkflowInvalid('Workflow change parent must be an object');
  const key=change.path.at(-1);
  if(change.op==='remove'){if(!Object.hasOwn(parent,key))throw new WorkflowInvalid('Workflow removal target missing');delete parent[key];}
  else parent[key]=change.value;
 }
 return state;
}
export async function saveStoredWorkflow(db:Database,bucket:Bucket,revision:number,state:any,empty:()=>any,validate:(next:any,previous:any)=>void){
 if(!Number.isSafeInteger(revision)||revision<0)throw new WorkflowInvalid('Invalid revision');
 const previous=await readStoredWorkflow(db,bucket,empty);
 if(previous.revision!==revision)throw new WorkflowConflict('State changed; reread before retrying');
 try{validate(state,previous.state);}catch(e){throw new WorkflowInvalid((e as Error).message);}
 // Archive the preceding state before replacing the pointer, including legacy inline data.
 const oldPointer=await archive(bucket,previous.state,revision);
 const pointer=await archive(bucket,state,revision+1,oldPointer.key);
 const result=await db.prepare('INSERT INTO workflow_state(id,revision,payload) VALUES(1,1,?) ON CONFLICT(id) DO UPDATE SET revision=workflow_state.revision+1,payload=excluded.payload WHERE workflow_state.revision=?').bind(JSON.stringify(pointer),revision).run();
 if(!result.meta.changes)throw new WorkflowConflict('State changed; reread before retrying');
 return {revision:revision+1};
}
