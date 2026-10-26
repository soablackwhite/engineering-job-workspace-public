import {authorizeRequest} from '@/db';
import {readWorkflow,saveWorkflow} from '@/lib/workflow-store';
import {applyWorkflowChanges,storageProtocol,WorkflowConflict,WorkflowInvalid} from '@/lib/workflow-storage-core';
const headers={'Cache-Control':'no-store'};
export async function GET(request:Request){const denied=authorizeRequest(request);if(denied)return denied;try{return Response.json({...await readWorkflow(),storage_protocol:storageProtocol},{headers});}catch{return Response.json({error:'Workflow unavailable'},{status:503,headers});}}
export async function PUT(request:Request){const denied=authorizeRequest(request);if(denied)return denied;
 if(request.headers.get('sec-fetch-site')==='cross-site')return Response.json({error:'Invalid origin'},{status:403});
 try{
  const raw=await request.text();if(new TextEncoder().encode(raw).length>1500000)return Response.json({error:'Workflow storage limit reached'},{status:413});
  const body=JSON.parse(raw);if(!Number.isSafeInteger(body.revision)||body.revision<0)return Response.json({error:'Invalid revision'},{status:400});
  if(Object.hasOwn(body,'changes')===Object.hasOwn(body,'state'))throw new WorkflowInvalid('Supply state or changes, exclusively');
  let state=body.state;
  if(Object.hasOwn(body,'changes')){const previous=await readWorkflow();if(body.revision!==previous.revision)throw new WorkflowConflict('State changed; reread before retrying');state=applyWorkflowChanges(previous.state,body.changes);}
  return Response.json(await saveWorkflow(body.revision,state),{headers});
 }catch(e){if(e instanceof WorkflowConflict||e instanceof WorkflowInvalid)return Response.json({error:e.message},{status:e instanceof WorkflowConflict?409:400,headers});return Response.json({error:'Workflow save failed. Read current state before retrying.'},{status:503,headers});}
}
export async function PATCH(request:Request){const denied=authorizeRequest(request);if(denied)return denied;
 if(request.headers.get('sec-fetch-site')==='cross-site')return Response.json({error:'Invalid origin'},{status:403});
 try{const body=await request.json() as {paused?:unknown};if(typeof body.paused!=='boolean')return Response.json({error:'Invalid pause value'},{status:400});
 const prev=await readWorkflow();prev.state.paused=body.paused;
 return Response.json(await saveWorkflow(prev.revision,prev.state),{headers});
 }catch{return Response.json({error:'Pause could not be saved'},{status:503});}
}
