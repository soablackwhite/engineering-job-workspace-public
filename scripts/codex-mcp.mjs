#!/usr/bin/env node
/** Stdio-to-loopback bridge. It reads only this workspace's private runtime token. */
import {lstat,readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import readline from 'node:readline';

export async function readRuntime(workspaceRoot) {
  const root=path.resolve(workspaceRoot),privateDir=path.join(root,'.private'),file=path.join(privateDir,'runtime.json');
  const folder=await lstat(privateDir),stat=await lstat(file);
  if(folder.isSymbolicLink()||!folder.isDirectory()||stat.isSymbolicLink()||!stat.isFile()||stat.size>16384)throw new Error('Local runtime configuration is unavailable.');
  if(process.platform!=='win32'&&((folder.mode&0o077)||(stat.mode&0o077)))throw new Error('Local runtime file permissions are too broad. Restart the local workspace.');
  const value=JSON.parse(await readFile(file,'utf8'));
  const origin=new URL(value.origin);
  if(origin.protocol!=='http:'||origin.hostname!=='127.0.0.1'||!origin.port||origin.username||origin.password||origin.pathname!=='/'||origin.search||origin.hash)throw new Error('Local runtime must use a loopback HTTP origin.');
  if(typeof value.mcpToken!=='string'||!/^[A-Za-z0-9_-]{32,256}$/.test(value.mcpToken))throw new Error('Local runtime token is invalid.');
  return {origin:origin.origin,mcpToken:value.mcpToken};
}

export async function forwardMessage(workspaceRoot,message,{fetchImpl=fetch}={}) {
  if(!message||typeof message!=='object'||Array.isArray(message)||message.jsonrpc!=='2.0'||typeof message.method!=='string')throw new Error('Invalid MCP request.');
  const {origin,mcpToken}=await readRuntime(workspaceRoot);
  const response=await fetchImpl(origin+'/mcp',{method:'POST',redirect:'error',signal:AbortSignal.timeout(15000),headers:{'Content-Type':'application/json','Accept':'application/json','Authorization':'Bearer '+mcpToken},body:JSON.stringify(message)});
  if(response.status===204||response.status===202)return null;
  if(!response.ok)throw new Error('Local workspace connection failed. Keep npm run local running and try again.');
  const raw=await response.text();
  if(Buffer.byteLength(raw)>512*1024)throw new Error('Local workspace response was too large.');
  return JSON.parse(raw);
}

export async function runBridge(workspaceRoot,{input=process.stdin,output=process.stdout,errors=process.stderr,fetchImpl=fetch}={}) {
  const lines=readline.createInterface({input,crlfDelay:Infinity});
  for await(const line of lines) {
    if(!line.trim())continue;
    let message;
    try {
      if(Buffer.byteLength(line)>256*1024)throw new Error('Request too large');
      message=JSON.parse(line);
      const response=await forwardMessage(workspaceRoot,message,{fetchImpl});
      if(response)output.write(JSON.stringify(response)+'\n');
    } catch {
      // Avoid reflecting exception text: paths, headers and token values stay private.
      errors.write('The local workspace is unavailable or rejected the request. Keep npm run local running.\n');
      if(message?.id!==undefined)output.write(JSON.stringify({jsonrpc:'2.0',id:message.id,error:{code:-32000,message:'Local workspace connection unavailable'}})+'\n');
    }
  }
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const args=process.argv.slice(2);
  if(args.length!==2||args[0]!=='--workspace'||!path.isAbsolute(args[1])) {
    process.stderr.write('Usage: node scripts/codex-mcp.mjs --workspace <absolute workspace folder>\n');
    process.exitCode=1;
  } else await runBridge(args[1]);
}
