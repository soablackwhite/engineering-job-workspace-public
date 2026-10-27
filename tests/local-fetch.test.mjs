import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspaceFetch} from '../lib/local-fetch.ts';

test('local writes obtain one private CSRF proof and preserve caller headers',async()=>{
 const calls=[];
 const request=createWorkspaceFetch(async(input,init)=>{
  calls.push([input,init]);
  return input==='/api/setup' ? Response.json({mode:'local',csrfToken:'synthetic-csrf'}) : Response.json({ok:true});
 });
 await request('/api/tracking',{method:'PATCH',headers:{'Content-Type':'application/json'},body:'{}'});
 await request('/api/workflow',{method:'PUT',body:'{}'});
 assert.equal(calls.length,3);
 assert.equal(calls[1][1].headers.get('X-Workspace-CSRF'),'synthetic-csrf');
 assert.equal(calls[1][1].headers.get('Content-Type'),'application/json');
});
test('reads and external destinations never request or receive local proof',async()=>{
 const calls=[];const request=createWorkspaceFetch(async(input,init)=>{calls.push([input,init]);return Response.json({ok:true});});
 await request('/api/workspace');
 await request('https://example.com/api/workflow',{method:'PUT'});
 await request('//example.com/api/tracking',{method:'PATCH'});
 assert.equal(calls.length,3);assert.ok(calls.every(([,init])=>!new Headers(init.headers).has('X-Workspace-CSRF')));
});
test('hosted mode keeps the existing request authentication unchanged',async()=>{
 const calls=[];const request=createWorkspaceFetch(async(input,init)=>{calls.push([input,init]);return input==='/api/setup'?new Response(null,{status:404}):Response.json({ok:true});});
 await request('/api/workflow',{method:'PUT'});
 assert.equal(calls.length,2);assert.ok(!new Headers(calls[1][1].headers).has('X-Workspace-CSRF'));
});
test('rejected writes clear stale proof without replaying an application mutation',async()=>{
 const calls=[];const request=createWorkspaceFetch(async(input,init)=>{calls.push([input,init]);return input==='/api/setup'?Response.json({mode:'local',csrfToken:'synthetic-csrf'}):new Response(null,{status:403});});
 await request('/api/workflow',{method:'PUT'});assert.equal(calls.length,2);
 await request('/api/workflow',{method:'PUT'});assert.equal(calls.length,4);
});
