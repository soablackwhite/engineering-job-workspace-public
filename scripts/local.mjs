#!/usr/bin/env node
/** Launch only the laptop gateway; no login, account or remote deployment. */
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {acquireLocalProcessLock} from '../local/process-lock.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const port = Number(process.env.JOB_WORKSPACE_PORT || 4310);
if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) {
  console.error('Choose a local port between 1024 and 65535.');
  process.exit(1);
}
process.umask(0o077);
let lock;
try { lock = await acquireLocalProcessLock(root); }
catch (error) { console.error(error.code?.startsWith('LOCAL_') ? error.message : 'Private local storage could not be opened.'); process.exit(1); }
const child = spawn(process.execPath, [path.join(root, 'node_modules', 'vinext', 'dist', 'cli.js'), 'dev', '--hostname', '127.0.0.1', '--port', String(port)], {
  cwd: root, stdio: 'inherit', env: {...process.env, JOB_WORKSPACE_LOCAL: '1', JOB_WORKSPACE_PORT: String(port)},
});
let startupFailed = false;
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { child.kill(signal); });
child.on('error', () => { startupFailed = true; console.error('The local workspace could not start. Run npm ci first.'); process.exitCode = 1; void lock.release(); });
child.on('exit', async (code, signal) => { await lock.release(); process.exitCode = startupFailed ? 1 : code ?? (signal ? 0 : 1); });
try { await lock.trackChild(child.pid); }
catch {
  startupFailed = true;
  console.error('The local server could not be tracked safely and is being stopped.');
  child.kill('SIGTERM');
  process.exitCode = 1;
  // Keep the launcher and its lock alive until the child actually exits.
}
if (!startupFailed) console.log(`Open http://127.0.0.1:${port}/setup to set up your private workspace.`);
