import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,stat,symlink,chmod} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {Readable,Writable} from 'node:stream';
import {discoverSource,snapshotSource,sourceSearch,sourceRead,validateSourceSnapshot,storeResume,approvedResume,codexCommand,createMcpDispatcher,limits} from '../local/connectors.mjs';
import {readRuntime,forwardMessage,runBridge} from '../scripts/codex-mcp.mjs';

async function fixture(t) {
  const root=await mkdtemp(path.join(os.tmpdir(),'workspace-connectors-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  const vault=path.join(root,'notes'),privateDir=path.join(root,'.private');
  await mkdir(vault);await mkdir(privateDir,{mode:0o700});await chmod(privateDir,0o700);
  return {root,vault,privateDir};
}
const pdf=Buffer.from('%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n');
const initialize=(clientName='codex-synthetic')=>({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-03-26',clientInfo:{name:clientName,version:'test'}}});
const call=(name,args={})=>({jsonrpc:'2.0',id:2,method:'tools/call',params:{name,arguments:args}});

test('source discovery lists names without importing bodies or linked/hidden notes',async t=>{
  const {root,vault,privateDir}=await fixture(t);
  await writeFile(path.join(vault,'Career.md'),'# Career\nA unique source sentence.');
  await writeFile(path.join(vault,'.hidden.md'),'Hidden content');
  await writeFile(path.join(root,'outside.md'),'Outside content');
  await symlink(path.join(root,'outside.md'),path.join(vault,'Linked.md'));
  const result=await discoverSource(vault);
  assert.equal(result.noteCount,1);assert.equal(result.notes[0].path,'Career.md');
  assert.ok(!JSON.stringify(result).includes('unique source sentence'));
  await assert.rejects(readFile(path.join(privateDir,'sources','current.json')),{code:'ENOENT'});
});

test('only selected notes are copied, hashed and read from the immutable snapshot',async t=>{
  const {vault,privateDir}=await fixture(t);
  await writeFile(path.join(vault,'Selected.md'),'# Selected\nSynthetic cloud delivery.');
  await writeFile(path.join(vault,'Unselected.md'),'Private unselected sentence.');
  const snapshot=await snapshotSource(privateDir,vault,['Selected.md']);
  assert.equal(snapshot.selectedCount,1);
  assert.deepEqual(await validateSourceSnapshot(privateDir),{snapshotId:snapshot.snapshotId,selectedCount:1});
  assert.match(snapshot.snapshotId,/^[a-f0-9]{64}$/);
  await writeFile(path.join(vault,'Selected.md'),'Source changed later.');
  const selected=await sourceRead(privateDir,snapshot.notes[0].id);
  assert.match(selected.content,/Synthetic cloud delivery/);
  assert.equal((await sourceSearch(privateDir,'cloud')).results.length,1);
  assert.equal((await sourceSearch(privateDir,'unselected')).results.length,0);
  const saved=JSON.parse(await readFile(path.join(privateDir,'sources','current.json'),'utf8'));
  assert.equal(saved.notes.length,1);
  if(process.platform!=='win32')assert.equal((await stat(path.join(privateDir,'sources',snapshot.snapshotId,saved.notes[0].file))).mode&0o777,0o600);
});

test('source selection rejects path escapes, links, duplicate notes and oversize bodies',async t=>{
  const {root,vault,privateDir}=await fixture(t);
  await writeFile(path.join(root,'outside.md'),'Outside selected folder');
  await writeFile(path.join(vault,'Small.md'),'Small');
  await symlink(path.join(root,'outside.md'),path.join(vault,'Linked.md'));
  for(const selected of [['../outside.md'],['Linked.md'],['Small.md','Small.md'],['.hidden.md']]) {
    await assert.rejects(snapshotSource(privateDir,vault,selected));
  }
  await writeFile(path.join(vault,'Large.md'),'x'.repeat(limits.noteBytes+1));
  await assert.rejects(snapshotSource(privateDir,vault,['Large.md']),{code:'SOURCE_SIZE'});
  await assert.rejects(snapshotSource(path.join(root,'not-private'),vault,['Small.md']),{code:'PRIVATE_DIRECTORY'});
});

test('snapshot tampering and symlinked private directories cannot expose other files',async t=>{
  const {root,vault,privateDir}=await fixture(t);
  await writeFile(path.join(vault,'Selected.md'),'Synthetic selected content.');
  const snapshot=await snapshotSource(privateDir,vault,['Selected.md']);
  const manifest=JSON.parse(await readFile(path.join(privateDir,'sources','current.json'),'utf8'));
  const saved=path.join(privateDir,'sources',snapshot.snapshotId,manifest.notes[0].file);
  await writeFile(saved,'Tampered');
  await assert.rejects(sourceRead(privateDir,snapshot.notes[0].id),{code:'SOURCE_CHANGED'});
  await assert.rejects(validateSourceSnapshot(privateDir),{code:'SOURCE_CHANGED'});
  const outside=path.join(root,'outside-private');await mkdir(outside);await writeFile(path.join(outside,'current.json'),JSON.stringify(manifest));
  await rm(path.join(privateDir,'sources'),{recursive:true});await symlink(outside,path.join(privateDir,'sources'));
  await assert.rejects(sourceRead(privateDir,snapshot.notes[0].id),{code:'SOURCE_UNAVAILABLE'});
});

test('CV storage verifies PDF bytes and never grants application approval',async t=>{
  const {privateDir}=await fixture(t);
  const result=await storeResume(privateDir,{name:'Example CV.pdf',data:pdf.toString('base64')});
  assert.equal(result.applicationReady,false);assert.equal(result.requiresReview,true);
  assert.deepEqual(await readFile(path.join(privateDir,'resume','approved.pdf')),pdf);
  assert.equal((await approvedResume(privateDir)).sha256,result.sha256);
  for(const value of [{name:'other.txt',data:pdf.toString('base64')},{name:'../resume.pdf',data:pdf.toString('base64')},{name:'resume.pdf',data:Buffer.from('not a PDF').toString('base64')},{name:'resume.pdf',data:'!!!!'}]) {
    await assert.rejects(storeResume(privateDir,value));
  }
  await writeFile(path.join(privateDir,'resume','approved.pdf'),'changed');
  await assert.rejects(approvedResume(privateDir),{code:'RESUME_CHANGED'});
});

test('MCP requires initialization and a real workspace_status call before connection proof',async t=>{
  const {privateDir}=await fixture(t),proof=[];
  const dispatcher=createMcpDispatcher(privateDir,{getStatus:async()=>({completed:false,checks:{storage:{status:'ready'},codex:{status:proof.length?'ready':'pending'}},csrfToken:'never-expose-this',codex:{command:'private command'},source:{label:'Career',root:'private-location',selectedCount:1},preferences:{roles:[' Example Engineer '],locations:['Example region'],sponsorship:true,salary:120000,token:'never-expose-preference-token'}}),onConnection:async item=>proof.push(item)});
  assert.equal((await dispatcher(call('workspace_status'))).error.code,-32002);
  assert.equal((await dispatcher(initialize())).result.protocolVersion,'2025-03-26');
  assert.equal(proof.length,0);
  const response=await dispatcher(call('workspace_status'));
  assert.equal(proof.length,1);assert.equal(proof[0].tool,'workspace_status');
  assert.ok(!JSON.stringify(response).includes('never-expose-this'));
  assert.ok(!JSON.stringify(response).includes('private-location'));
  assert.ok(!JSON.stringify(response).includes('never-expose-preference-token'));
  const status=JSON.parse(response.result.content[0].text);
  assert.equal(status.checks.codex.status,'ready');
  assert.deepEqual(status.applications,{paused:true,submissionAuthorized:false});
  assert.deepEqual(status.preferences,{roles:['Example Engineer'],locations:['Example region'],sponsorship:true,salary:120000});
  assert.equal((await dispatcher(call('unlisted'))).error.code,-32602);
  assert.equal((await dispatcher(call('workspace_status',{extra:true}))).error.code,-32602);
});

test('workspace status does not expose malformed preferences or unrelated private fields',async t=>{
  const {privateDir}=await fixture(t);
  for(const preferences of [{roles:['Example'],locations:['Example region'],sponsorship:'yes',secret:'synthetic-private-field'},{roles:[{nested:'synthetic-private-field'}],locations:['Example region'],sponsorship:true},{roles:['Example'],locations:['Example region'],sponsorship:false,salary:-1}]) {
    const dispatcher=createMcpDispatcher(privateDir,{getStatus:async()=>({preferences})});
    await dispatcher(initialize('synthetic-inspector'));
    const response=await dispatcher(call('workspace_status'));
    assert.equal(JSON.parse(response.result.content[0].text).preferences,null);
    assert.ok(!JSON.stringify(response).includes('synthetic-private-field'));
  }
});

test('non-Codex clients do not satisfy the Codex check; workspace composition preserves callback gates',async t=>{
  const {privateDir}=await fixture(t),proof=[],delegated=[];
  const dispatcher=createMcpDispatcher(privateDir,{onConnection:async item=>proof.push(item),workspaceTools:[{name:'application_jobs',inputSchema:{type:'object',properties:{query:{type:'string'}},additionalProperties:false}}],callWorkspaceTool:async(name,args)=>{delegated.push({name,args});return {content:[{type:'text',text:'Synthetic jobs'}]};}});
  await dispatcher(initialize('synthetic-inspector'));
  await dispatcher(call('workspace_status'));assert.equal(proof.length,0);
  const listed=await dispatcher({jsonrpc:'2.0',id:3,method:'tools/list'});
  assert.ok(listed.result.tools.some(tool=>tool.name==='application_jobs'));
  const result=await dispatcher(call('application_jobs',{query:'example'}));
  assert.equal(result.result.content[0].text,'Synthetic jobs');assert.equal(delegated.length,1);assert.equal(proof.length,0);
});

test('stdio bridge reads restrictive loopback configuration and sends no token in URL or output',async t=>{
  const {root,privateDir}=await fixture(t),token='synthetic-runtime-token-for-private-tests',requests=[];
  const file=path.join(privateDir,'runtime.json');
  await writeFile(file,JSON.stringify({origin:'http://127.0.0.1:4310',mcpToken:token}),{mode:0o600});await chmod(file,0o600);
  assert.equal((await readRuntime(root)).origin,'http://127.0.0.1:4310');
  const fetchImpl=async(url,options)=>{requests.push({url,options});return new Response(JSON.stringify({jsonrpc:'2.0',id:1,result:{ok:true}}),{headers:{'Content-Type':'application/json'}});};
  assert.equal((await forwardMessage(root,initialize(),{fetchImpl})).result.ok,true);
  assert.equal(requests[0].url,'http://127.0.0.1:4310/mcp');assert.equal(requests[0].options.headers.Authorization,'Bearer '+token);
  assert.equal(requests[0].options.redirect,'error');
  let output='',errors='';
  const sink=value=>new Writable({write(chunk,encoding,next){value(chunk.toString());next();}});
  await runBridge(root,{input:Readable.from([JSON.stringify(initialize())+'\n']),output:sink(chunk=>{output+=chunk;}),errors:sink(chunk=>{errors+=chunk;}),fetchImpl});
  assert.ok(!output.includes(token));assert.equal(errors,'');assert.equal(JSON.parse(output).result.ok,true);
  const nodePath=path.join(root,'synthetic-node-binary');
  const command=codexCommand(path.join(root,"workspace with 'quote'"),{nodePath});
  assert.match(command,/codex mcp add job-workspace -- /);assert.ok(command.includes(nodePath));assert.ok(!command.includes(token));
  assert.throws(()=>codexCommand(root,{nodePath:'relative-node'}),{code:'CODEX_RUNTIME'});
});

test('bridge rejects non-loopback, redirectable credential URLs and loose private permissions',async t=>{
  const {root,privateDir}=await fixture(t),file=path.join(privateDir,'runtime.json'),token='synthetic-runtime-token-for-private-tests';
  for(const origin of ['https://example.com','http://localhost:4310','http://127.0.0.1:4310/path','http://127.0.0.1:4310?redirect=1']) {
    await writeFile(file,JSON.stringify({origin,mcpToken:token}),{mode:0o600});await chmod(file,0o600);
    await assert.rejects(readRuntime(root));
  }
  await writeFile(file,JSON.stringify({origin:'http://127.0.0.1:4310',mcpToken:token}),{mode:0o600});await chmod(file,0o644);
  if(process.platform!=='win32')await assert.rejects(readRuntime(root),/permissions/);
});
