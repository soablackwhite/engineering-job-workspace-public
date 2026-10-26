/** Stateless MCP transport. Site dispatch owns authentication; reject missing identity here too. */
export const tools = [
 {name:'application_context',description:'Read current cloud handoff manifest, constraints, and selected career notes. No local computer access.',inputSchema:{type:'object',properties:{note:{type:'string'}},additionalProperties:false},annotations:{readOnlyHint:true}},
 {name:'application_cv',description:'Retrieve the approved current PDF as an embedded binary resource, with its SHA-256. A saved LinkedIn resume is not fresh-upload proof.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true}},
 {name:'application_jobs',description:'Search the live CRM and statuses. Defaults to 20 compact rows; use application_job for exact holds and attempt history.',inputSchema:{type:'object',properties:{query:{type:'string'},status:{type:'string'},limit:{type:'integer',minimum:1,maximum:50}},additionalProperties:false},annotations:{readOnlyHint:true}},
 {name:'application_job',description:'Read one existing job including attempts, submission status and holds, plus the live revision. Accepts CRM ID or exact LinkedIn job ID.',inputSchema:{type:'object',required:['id'],properties:{id:{type:'string'}},additionalProperties:false},annotations:{readOnlyHint:true}},
 {name:'application_execution',description:'Read cloud executor checkpoint and pending messages. It is not proof of an active cloud browser.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true}},
 {name:'application_checkpoint',description:'Write a cloud executor checkpoint or exact question for the coordinator, with revision conflict detection. Cannot unpause, submit, or change job status.',inputSchema:{type:'object',required:['revision','checkpoint'],properties:{revision:{type:'integer',minimum:0},checkpoint:{type:'object'}},additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:false}},
 {name:'application_update_job',description:'Persist an existing workflow job using expected revision and existing server validation. Live execution remains gated; immutable attempts and holds cannot be discarded. Read back after writing.',inputSchema:{type:'object',required:['revision','id','job'],properties:{revision:{type:'integer',minimum:0},id:{type:'string'},job:{type:'object'}},additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:false}}
];
export async function handleMcp(request:Request,call:(name:string,args:Record<string,unknown>)=>Promise<unknown>){
 const headers={'Cache-Control':'no-store','Content-Type':'application/json'};
 if(!request.headers.get('oai-authenticated-user-id'))return Response.json({error:'Authenticated Site identity required'},{status:401,headers});
 if(request.headers.get('sec-fetch-site')==='cross-site')return Response.json({error:'Invalid origin'},{status:403,headers});
 if(Number(request.headers.get('content-length')||0)>1500000)return new Response(null,{status:413,headers});
 let body:any;try{const raw=await request.text();if(new TextEncoder().encode(raw).length>1500000)return new Response(null,{status:413,headers});body=JSON.parse(raw);}catch{return Response.json({jsonrpc:'2.0',id:null,error:{code:-32700,message:'Invalid JSON'}},{headers});}
 if(!body||Array.isArray(body)||body.jsonrpc!=='2.0'||typeof body.method!=='string')return Response.json({jsonrpc:'2.0',id:body?.id??null,error:{code:-32600,message:'Invalid request'}},{headers});
 const reply=(result:unknown)=>Response.json({jsonrpc:'2.0',id:body.id??null,result},{headers});
 if(body.method==='server/discover')return reply({supportedVersions:['2026-07-28'],capabilities:{tools:{}}});
 if(body.method==='initialize')return reply({protocolVersion:'2025-03-26',capabilities:{tools:{}},serverInfo:{name:'private-application-crm',version:'1.0.0'}});
 if(body.method==='notifications/initialized')return new Response(null,{status:204,headers});
 if(body.method==='ping')return reply({});
 if(body.method==='tools/list')return reply({tools});
 if(body.method!=='tools/call')return Response.json({jsonrpc:'2.0',id:body.id??null,error:{code:-32601,message:'Method not found'}},{headers});
 const name=body.params?.name,args=body.params?.arguments??{};
 if(!tools.some(t=>t.name===name)||!args||typeof args!=='object'||Array.isArray(args))return reply({isError:true,content:[{type:'text',text:'Unknown tool or invalid arguments'}]});
 try{const result:any=await call(name,args);return reply(result?.content?result:{content:[{type:'text',text:JSON.stringify(result)}]});}
 catch(error){return reply({isError:true,content:[{type:'text',text:(error as Error).message}]});}
}
