/** Laptop-only gateway. This module is never installed into a hosted Worker. */
import {randomBytes, timingSafeEqual} from 'node:crypto';
import {mkdir, readFile, writeFile, rename, chmod, lstat} from 'node:fs/promises';
import path from 'node:path';
import {ConnectorError} from './connectors.mjs';
import {tools as workspaceTools} from '../lib/cloud-mcp.ts';

const steps = ['welcome', 'sources', 'resume', 'preferences', 'codex', 'review', 'complete'];
const cookieName = 'job_workspace_session';
const jsonLimit = 15 * 1024 * 1024;
const random = () => randomBytes(32).toString('hex');
const equal = (a, b) => {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const left = Buffer.from(a), right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
};
const loopback = address => ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(address);

export class SetupError extends Error {
  constructor(message, code = 'INVALID_SETUP', status = 400) { super(message); this.code = code; this.status = status; }
}

async function privateDirectory(directory) {
  await mkdir(directory, {recursive: true, mode: 0o700});
  if ((await lstat(directory)).isSymbolicLink()) throw new SetupError('Private storage must be a real directory.', 'UNSAFE_STORAGE');
  await chmod(directory, 0o700);
}

export async function writePrivateJSON(filename, data) {
  await privateDirectory(path.dirname(filename));
  try { if ((await lstat(filename)).isSymbolicLink()) throw new SetupError('Private storage contains an unsafe link.', 'UNSAFE_STORAGE'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const temporary = filename + '.' + random() + '.tmp';
  await writeFile(temporary, JSON.stringify(data, null, 2) + '\n', {mode: 0o600, flag: 'wx'});
  await rename(temporary, filename);
  await chmod(filename, 0o600);
}

async function readJSON(filename, fallback) {
  try {
    const info = await lstat(filename);
    if (info.isSymbolicLink() || !info.isFile()) throw new SetupError('Private storage contains an unsafe link.', 'UNSAFE_STORAGE');
    if (info.size > 4 * 1024 * 1024) throw new SetupError('Private setup state is too large.', 'INVALID_LOCAL_STATE');
    return JSON.parse(await readFile(filename, 'utf8'));
  } catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
}

function cookies(value = '') {
  return Object.fromEntries(value.split(';').map(item => item.trim().split('=')).filter(parts => parts.length === 2));
}

/** The Vite/Cloudflare bridge reads rawHeaders rather than the normalized map. */
function gatewayIdentity(request, value) {
  const name = 'oai-authenticated-user-id';
  delete request.headers[name];
  const raw = request.rawHeaders || [];
  const clean = [];
  for (let i = 0; i < raw.length; i += 2) if (String(raw[i]).toLowerCase() !== name) clean.push(raw[i], raw[i + 1]);
  if (value !== undefined) { request.headers[name] = value; clean.push(name, value); }
  request.rawHeaders = clean;
}

/** Host is validated in addition to peer address to reject DNS rebinding. */
export function authorizeLocalRequest(request, secrets, {write = false, mcp = false} = {}) {
  if (!loopback(request.socket?.remoteAddress)) throw new SetupError('Local connections only.', 'LOCAL_ONLY', 403);
  const expected = new URL(secrets.origin);
  if (request.headers.host !== expected.host) throw new SetupError('Unexpected local address.', 'INVALID_HOST', 403);
  const origin = request.headers.origin;
  if (origin && origin !== secrets.origin) throw new SetupError('Open this page directly on your laptop.', 'INVALID_ORIGIN', 403);
  if (request.headers['sec-fetch-site'] === 'cross-site') throw new SetupError('Cross-site requests are blocked.', 'INVALID_ORIGIN', 403);
  if (mcp) {
    if (origin) throw new SetupError('Use the private Codex bridge.', 'MCP_BROWSER_DENIED', 403);
    if (!equal(request.headers.authorization, 'Bearer ' + secrets.mcpToken)) throw new SetupError('Private bridge authentication required.', 'UNAUTHORIZED', 401);
    return;
  }
  if (!equal(cookies(request.headers.cookie)[cookieName], secrets.sessionToken)) throw new SetupError('Reload the local workspace to start a session.', 'LOCAL_SESSION_REQUIRED', 401);
  if (write && (origin !== secrets.origin || !equal(request.headers['x-workspace-csrf'], secrets.csrfToken))) throw new SetupError('Refresh the page and try again.', 'CSRF_REQUIRED', 403);
}

function respond(response, status, value) {
  response.statusCode = status;
  response.setHeader('Content-Type', 'application/json; charset=utf-8');
  response.setHeader('Cache-Control', 'private, no-store');
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.end(JSON.stringify(value));
}

async function body(request, maximum = jsonLimit) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers['content-type'] || '')) throw new SetupError('Send JSON data.', 'JSON_REQUIRED', 415);
  if (Number(request.headers['content-length'] || 0) > maximum) throw new SetupError('This file is too large.', 'BODY_TOO_LARGE', 413);
  let length = 0, chunks = [];
  for await (const chunk of request) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    length += bytes.length;
    if (length > maximum) throw new SetupError('This file is too large.', 'BODY_TOO_LARGE', 413);
    chunks.push(bytes);
  }
  let value;
  try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw new SetupError('This request could not be read.', 'INVALID_JSON'); }
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new SetupError('A setup action is required.');
  return value;
}

const emptyState = () => ({schemaVersion: 1, revision: 0, step: 'welcome', visited: ['welcome'], completed: false, paused: true});
const ready = detail => ({status: 'ready', detail});
const pending = detail => ({status: 'pending', detail});
function validPreferences(values) {
  const list = v => Array.isArray(v) && v.length > 0 && v.length <= 12 && v.every(s => typeof s === 'string' && s.trim().length > 0 && s.length <= 120);
  return values && list(values.roles) && list(values.locations) && typeof values.sponsorship === 'boolean' &&
    (values.salary === undefined || (Number.isSafeInteger(values.salary) && values.salary >= 0 && values.salary <= 10000000));
}

/**
 * Creates state only; does not register Codex, read a vault or enable applications.
 * @param {{root:string, port?:number, connectors?:any, bootstrapStorage?:(context:{root:string,directory:string})=>Promise<void>}} options
 */
export async function createLocalRuntime({root, port = 4310, connectors: suppliedConnectors, bootstrapStorage = bootstrapLocalStorage}) {
  root = path.resolve(root);
  if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) throw new SetupError('Choose a local port from 1024 to 65535.');
  const directory = path.join(root, '.private');
  await privateDirectory(directory);
  const secretsFile = path.join(directory, 'local-secrets.json');
  let persistent = await readJSON(secretsFile, null);
  if (!persistent) {
    persistent = {ownerIdentity: random(), mcpToken: random()};
    await writePrivateJSON(secretsFile, persistent);
  }
  if (![persistent.ownerIdentity, persistent.mcpToken].every(v => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v))) throw new SetupError('Private connection settings need repair.', 'INVALID_LOCAL_KEYS');
  const secrets = {...persistent, origin: `http://127.0.0.1:${port}`, sessionToken: random(), csrfToken: random()};
  await writePrivateJSON(path.join(directory, 'runtime.json'), {origin: secrets.origin, mcpToken: secrets.mcpToken});
  const filename = path.join(directory, 'onboarding.json');
  let state = await readJSON(filename, emptyState());
  if (state.schemaVersion !== 1 || !Number.isSafeInteger(state.revision) || !steps.includes(state.step) || state.paused !== true) throw new SetupError('Private setup state needs review.', 'INVALID_LOCAL_STATE');
  if (!Array.isArray(state.visited)) state.visited = [state.step];
  if (state.revision === 0) await writePrivateJSON(filename, state);
  // A previous process's connection proof is never a current live connection.
  let codexConnection = null;
  const storage = {status: 'pending', detail: 'Preparing private local storage.'};
  try { await bootstrapStorage({root, directory}); Object.assign(storage, ready('Private local database and documents are ready.')); }
  catch { Object.assign(storage, {status: 'error', detail: 'Local storage could not start. Check the local terminal and retry.'}); }
  const connectors = suppliedConnectors || await import('./connectors.mjs');
  let verifiedInputs = {source: pending('Choose the notes you want to share.'), resume: pending('Add the CV you want to use.')};
  async function refreshInputs() {
    const checks = {source: pending('Choose the notes you want to share.'), resume: pending('Add the CV you want to use.')};
    if (state.source?.selectedCount > 0) {
      try {
        const source = await connectors.validateSourceSnapshot(directory);
        if (source.snapshotId !== state.source.snapshotId || source.selectedCount !== state.source.selectedCount) throw Error('Changed snapshot');
        checks.source = ready(`${source.selectedCount} selected notes verified in private storage.`);
      } catch { checks.source = {status: 'error', detail: 'The saved notes changed or are missing. Select and confirm your sources again.'}; }
    }
    if (state.resume?.sha256) {
      try {
        const resume = await connectors.approvedResume(directory);
        if (resume.sha256 !== state.resume.sha256) throw Error('Changed CV');
        checks.resume = ready('Your private PDF and checksum have been verified.');
      } catch { checks.resume = {status: 'error', detail: 'The saved CV changed or is missing. Upload it again.'}; }
    }
    verifiedInputs = checks;
  }
  await refreshInputs();
  let mutation = Promise.resolve();
  const update = operation => {
    const result = mutation.then(async () => {
      const next = structuredClone(state);
      await operation(next);
      next.revision = state.revision + 1;
      next.paused = true;
      await writePrivateJSON(filename, next);
      state = next;
    });
    mutation = result.catch(() => {});
    return result;
  };
  function checkState() {
    return {
      storage: {...storage},
      source: {...verifiedInputs.source},
      resume: {...verifiedInputs.resume},
      codex: codexConnection ? ready('Codex reached this workspace and checked its status.') : pending('Run the connection command, then ask Codex to check this workspace.'),
    };
  }
  function status() {
    const checks = checkState();
    const completeNow = state.completed && validPreferences(state.preferences) && Object.values(checks).every(check => check.status === 'ready');
    return {
      mode: 'local', revision: state.revision, step: completeNow ? 'complete' : state.completed ? 'codex' : state.step,
      completed: Boolean(completeNow), paused: true, checks,
      ...(state.source ? {source: {label: state.source.label, path: state.source.root, noteCount: state.source.noteCount,
        selectedCount: state.source.selectedCount || 0, notes: state.source.notes || [], selected: state.source.selected || []}} : {}),
      ...(state.resume ? {resume: {name: state.resume.name, sha256: state.resume.sha256, bytes: state.resume.bytes}} : {}),
      ...(state.preferences ? {preferences: state.preferences} : {}),
      codex: {command: connectors.codexCommand(root), status: codexConnection ? 'ready' : 'pending'},
    };
  }
  const dispatcher = connectors.createMcpDispatcher(directory, {
    workspaceTools,
    callWorkspaceTool: async (name, args) => {
      const result = await fetch(secrets.origin + '/mcp?workspace=1', {method: 'POST',
        headers: {'Authorization': 'Bearer ' + secrets.mcpToken, 'Content-Type': 'application/json'},
        body: JSON.stringify({jsonrpc: '2.0', id: 1, method: 'tools/call', params: {name, arguments: args}})});
      if (!result.ok) throw new SetupError('The local CRM is unavailable.', 'LOCAL_CRM_UNAVAILABLE', 503);
      const value = await result.json();
      if (value.error) throw new SetupError('The local CRM rejected this request.', 'LOCAL_CRM_REJECTED', 422);
      return value.result;
    },
    getStatus: async () => { await refreshInputs(); return status(); },
    onConnection: async connection => {
      codexConnection = connection;
      await update(next => { next.codex = {verifiedAt: connection.verifiedAt, clientName: connection.clientName}; });
    },
  });
  async function setup(action) {
    await update(async next => {
      if (!Number.isSafeInteger(action.revision) || action.revision !== state.revision) throw new SetupError('Setup changed. Your latest progress has been reloaded.', 'REVISION_CONFLICT', 409);
      const advance = step => { next.step = step; if (!next.visited.includes(step)) next.visited.push(step); next.completed = false; };
      if (action.action === 'continue') {
        if (next.step === 'welcome') advance('sources');
        else if (next.step === 'resume') {
          await refreshInputs();
          if (checkState().resume.status !== 'ready') throw new SetupError('Upload your CV again before continuing.', 'STEP_INCOMPLETE', 422);
          advance('preferences');
        }
        else if (next.step === 'codex' && codexConnection) advance('review');
        else throw new SetupError('Finish this step before continuing.', 'STEP_INCOMPLETE', 422);
      } else if (action.action === 'source') {
        if (typeof action.path !== 'string' || !action.path.trim()) throw new SetupError('Choose a notes folder.');
        const discovery = await connectors.discoverSource(action.path.trim());
        if (action.selected !== undefined) {
          if (!Array.isArray(action.selected) || !action.selected.length || action.selected.some(v => typeof v !== 'string')) throw new SetupError('Select at least one note.');
          const displayed = new Set(discovery.notes.map(note => note.path));
          if (action.selected.some(name => !displayed.has(name))) throw new SetupError('Choose only notes from the displayed folder.', 'SOURCE_SELECTION');
          const snapshot = await connectors.snapshotSource(directory, discovery.root, action.selected);
          next.source = {...snapshot, selected: [...action.selected]};
          advance('resume');
        } else {
          next.source = {...discovery, selectedCount: 0, selected: []};
          advance('sources');
        }
      } else if (action.action === 'resume') {
        next.resume = await connectors.storeResume(directory, {name: action.name, data: action.data});
        advance('preferences');
      } else if (action.action === 'preferences') {
        const values = action.preferences || action;
        if (!validPreferences(values)) throw new SetupError('Check your role, location and sponsorship choices.');
        next.preferences = {roles: values.roles.map(v => v.trim()), locations: values.locations.map(v => v.trim()), sponsorship: values.sponsorship, ...(values.salary !== undefined ? {salary: values.salary} : {})};
        advance('codex');
      } else if (action.action === 'codex') {
        if (!next.source?.selectedCount || !next.resume?.sha256 || !next.preferences) throw new SetupError('Finish your notes, CV and preferences first.', 'STEP_INCOMPLETE', 422);
        advance('codex');
      } else if (action.action === 'back') {
        const target = action.step || steps[Math.max(0, steps.indexOf(next.step) - 1)];
        if (!steps.includes(target) || target === 'complete' || !next.visited.includes(target)) throw new SetupError('That step is not available yet.');
        advance(target);
      } else if (action.action === 'complete') {
        await refreshInputs();
        if (!validPreferences(next.preferences) || !Object.values(checkState()).every(check => check.status === 'ready')) throw new SetupError('A connection still needs attention.', 'CHECKS_INCOMPLETE', 422);
        next.completed = true; next.step = 'complete';
      } else throw new SetupError('Unknown setup action.');
    });
    await refreshInputs();
    return status();
  }
  function errorResponse(response, error) {
    if (error instanceof SetupError) return respond(response, error.status, {error: error.message, code: error.code});
    if (error instanceof ConnectorError) return respond(response, 422, {error: error.message, code: error.code});
    // Do not return filesystem paths, note content, tokens or subprocess output.
    return respond(response, 422, {error: 'This step could not be verified. Check the selected file or folder and try again.', code: 'VERIFICATION_FAILED'});
  }
  async function middleware(request, response, next) {
    const requestURL = new URL(request.url || '/', secrets.origin);
    const pathname = requestURL.pathname;
    // Remove caller-controlled gateway identities before any request reaches the Worker.
    gatewayIdentity(request);
    try {
      if (!loopback(request.socket?.remoteAddress) || request.headers.host !== new URL(secrets.origin).host) throw new SetupError('Use the local address shown in your terminal.', 'LOCAL_ONLY', 403);
      const decoded = decodeURIComponent(pathname);
      if (/(?:^|\/)(?:\.private|\.git|\.openai)(?:\/|$)|(?:^|\/)\.env[^/]*(?:\/|$)|\/workflow\/runtime(?:\/|$)/.test(decoded)) throw new SetupError('Private files are not served by the local website.', 'PRIVATE_FILE_DENIED', 403);
      if (pathname === '/mcp') {
        authorizeLocalRequest(request, secrets, {mcp: true});
        if (request.method !== 'POST') return respond(response, 405, {error: 'Use the Codex connection command.', code: 'METHOD_NOT_ALLOWED'});
        if (requestURL.searchParams.get('workspace') === '1') {
          gatewayIdentity(request, secrets.ownerIdentity);
          return next();
        }
        const result = await dispatcher(await body(request, 1024 * 1024));
        if (result === null) { response.statusCode = 202; response.end(); return; }
        return respond(response, 200, result);
      }
      if (pathname.startsWith('/api/')) {
        authorizeLocalRequest(request, secrets, {write: !['GET', 'HEAD'].includes(request.method)});
        if (pathname === '/api/setup') {
          if (request.method === 'GET') { await refreshInputs(); return respond(response, 200, {...status(), csrfToken: secrets.csrfToken}); }
          if (request.method === 'POST') return respond(response, 200, {...await setup(await body(request)), csrfToken: secrets.csrfToken});
          return respond(response, 405, {error: 'Unsupported setup method.', code: 'METHOD_NOT_ALLOWED'});
        }
        gatewayIdentity(request, secrets.ownerIdentity);
      } else {
        const documentNavigation = request.method === 'GET' && ['/', '/setup', '/workflow'].includes(pathname) &&
          request.headers['sec-fetch-mode'] === 'navigate' && request.headers['sec-fetch-dest'] === 'document';
        if (!documentNavigation && request.headers.origin && request.headers.origin !== secrets.origin) throw new SetupError('Cross-site requests are blocked.', 'INVALID_ORIGIN', 403);
        if (!documentNavigation && request.headers['sec-fetch-site'] === 'cross-site') throw new SetupError('Cross-site requests are blocked.', 'INVALID_ORIGIN', 403);
        response.setHeader('X-Frame-Options', 'DENY');
        response.setHeader('Content-Security-Policy', "frame-ancestors 'none'");
        if (request.method === 'GET' && String(request.headers.accept || '').includes('text/html')) response.setHeader('Set-Cookie', `${cookieName}=${secrets.sessionToken}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200`);
      }
      return next();
    } catch (error) { return errorResponse(response, error); }
  }
  return {middleware, ownerIdentity: secrets.ownerIdentity, privateDirectory: directory, origin: secrets.origin,
    // Exposed only to tests/configuration code, never serialized to a browser response.
    security: secrets, status, setup, refreshInputs, close: async () => { await mutation; }};
}

export function localWorkspacePlugin(runtime) {
  return {name: 'private-local-workspace', enforce: 'pre', configureServer(server) {
    server.middlewares.use(runtime.middleware);
    server.httpServer?.once('close', () => { void runtime.close(); });
  }};
}

export async function bootstrapLocalStorage({root, directory}) {
  const configPath = path.join(directory, 'wrangler-bootstrap.json');
  await writePrivateJSON(configPath, {name: 'job-workspace-local', compatibility_date: '2026-10-26', compatibility_flags: ['nodejs_compat'],
    d1_databases: [{binding: 'DB', database_name: 'job-workspace-local', database_id: '00000000-0000-4000-8000-000000000000'}],
    r2_buckets: [{binding: 'DOCUMENTS', bucket_name: 'job-workspace-local-documents'}]});
  const {getPlatformProxy} = await import('wrangler');
  const platform = await getPlatformProxy({configPath, persist: {path: path.join(directory, 'storage', 'v3')}, remoteBindings: false, envFiles: []});
  try {
    await platform.env.DB.exec("CREATE TABLE IF NOT EXISTS tracking (id text PRIMARY KEY NOT NULL,status text DEFAULT 'To review' NOT NULL,notes text DEFAULT '' NOT NULL,follow_up text DEFAULT '' NOT NULL,updated_at text NOT NULL); CREATE TABLE IF NOT EXISTS workflow_state (id integer PRIMARY KEY NOT NULL,revision integer NOT NULL,payload text NOT NULL);");
    await platform.env.DB.prepare('SELECT 1 AS ready').first();
    await platform.env.DOCUMENTS.put('local/storage-check.json', '{}', {httpMetadata: {contentType: 'application/json'}});
    if (!await platform.env.DOCUMENTS.get('local/storage-check.json')) throw new Error('Local document store unavailable');
  } finally { await platform.dispose(); }
}
