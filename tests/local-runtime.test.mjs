import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, stat, rm, symlink, mkdir} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {Readable} from 'node:stream';
import {authorizeLocalRequest,createLocalRuntime} from '../local/runtime.mjs';

const security = {origin: 'http://127.0.0.1:4310', sessionToken: 'synthetic-session', csrfToken: 'synthetic-csrf', mcpToken: 'synthetic-bridge'};
const request = (headers = {}, method = 'GET', url = '/api/setup', data) => {
  const req = Readable.from(data === undefined ? [] : [JSON.stringify(data)]);
  const normalized = {host: '127.0.0.1:4310', ...headers};
  Object.assign(req, {headers: normalized, rawHeaders: Object.entries(normalized).flat(), method, url, socket: {remoteAddress: '127.0.0.1'}});
  return req;
};
const browser = (overrides = {}) => ({cookie: 'job_workspace_session=synthetic-session', ...overrides});

test('local gateway requires real loopback peer, exact host, and a browser session', () => {
  assert.throws(() => authorizeLocalRequest(request(), security), error => error.code === 'LOCAL_SESSION_REQUIRED');
  assert.throws(() => authorizeLocalRequest(request(browser({host: 'attacker.example.com'})), security), error => error.code === 'INVALID_HOST');
  const remote = request(browser()); remote.socket.remoteAddress = '192.0.2.10';
  assert.throws(() => authorizeLocalRequest(remote, security), error => error.code === 'LOCAL_ONLY');
  assert.doesNotThrow(() => authorizeLocalRequest(request(browser()), security));
});

test('same-origin and CSRF protection are required for every browser write', () => {
  assert.throws(() => authorizeLocalRequest(request(browser()), security, {write: true}), error => error.code === 'CSRF_REQUIRED');
  assert.throws(() => authorizeLocalRequest(request(browser({origin: 'https://attacker.example.com', 'x-workspace-csrf': security.csrfToken})), security, {write: true}), error => error.code === 'INVALID_ORIGIN');
  assert.throws(() => authorizeLocalRequest(request(browser({origin: security.origin, 'x-workspace-csrf': 'wrong'})), security, {write: true}), error => error.code === 'CSRF_REQUIRED');
  assert.doesNotThrow(() => authorizeLocalRequest(request(browser({origin: security.origin, 'x-workspace-csrf': security.csrfToken})), security, {write: true}));
});

test('MCP uses a private bearer and cannot inherit browser or cross-origin access', () => {
  assert.throws(() => authorizeLocalRequest(request(browser()), security, {mcp: true}), error => error.code === 'UNAUTHORIZED');
  assert.throws(() => authorizeLocalRequest(request({authorization: 'Bearer ' + security.mcpToken, origin: security.origin}), security, {mcp: true}), error => error.code === 'MCP_BROWSER_DENIED');
  assert.doesNotThrow(() => authorizeLocalRequest(request({authorization: 'Bearer ' + security.mcpToken}), security, {mcp: true}));
});

async function fixture(t, {bootstrapStorage = async () => {}} = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'workspace-runtime-test-'));
  t.after(async () => { await rm(root, {recursive: true, force: true}); });
  let dispatcherOptions;
  const valid = {source: true, resume: true};
  const runtime = await createLocalRuntime({root, bootstrapStorage, connectors: {
    codexCommand: () => 'codex mcp add job-workspace -- node synthetic-bridge.mjs',
    discoverSource: async input => ({root: input, label: 'Example notes', noteCount: 2, notes: [{path: 'career.md', title: 'Example career'}]}),
    snapshotSource: async (_private, sourceRoot, selected) => ({root: sourceRoot, label: 'Example notes', noteCount: 2, selectedCount: selected.length, notes: selected.map(name => ({path: name, title: 'Example career'})), snapshotId: 'synthetic-snapshot'}),
    storeResume: async () => ({name: 'example-resume.pdf', sha256: 'a'.repeat(64), bytes: 12}),
    validateSourceSnapshot: async () => { if (!valid.source) throw Error('Synthetic missing source'); return {snapshotId: 'synthetic-snapshot', selectedCount: 1}; },
    approvedResume: async () => { if (!valid.resume) throw Error('Synthetic changed resume'); return {name: 'example-resume.pdf', sha256: 'a'.repeat(64), bytes: 12}; },
    createMcpDispatcher: (_private, options) => { dispatcherOptions = options; return async () => ({jsonrpc: '2.0', id: 1, result: {}}); },
  }});
  return {runtime, root, valid, get dispatcherOptions() { return dispatcherOptions; }};
}

function response() {
  return {statusCode: 200, headers: {}, setHeader(name, value) { this.headers[name.toLowerCase()] = value; }, end(value) { this.body = value; }};
}

test('private files have restrictive permissions and no credentials enter setup responses', async t => {
  const {runtime, root} = await fixture(t);
  assert.equal((await stat(path.join(root, '.private'))).mode & 0o777, 0o700);
  for (const name of ['local-secrets.json', 'runtime.json', 'onboarding.json']) assert.equal((await stat(path.join(root, '.private', name))).mode & 0o777, 0o600);
  const data = JSON.stringify(runtime.status());
  for (const value of [runtime.security.mcpToken, runtime.security.ownerIdentity, runtime.security.sessionToken, runtime.security.csrfToken]) assert.equal(data.includes(value), false);
  assert.equal(runtime.status().paused, true);
});

test('discovery, selected snapshot, resume and preferences do not fake Codex completion', async t => {
  const item = await fixture(t), {runtime} = item;
  const action = data => runtime.setup({...data, revision: runtime.status().revision});
  await action({action: 'continue'});
  await action({action: 'source', path: 'synthetic-vault'});
  assert.equal(runtime.status().step, 'sources');
  assert.equal(runtime.status().checks.source.status, 'pending');
  await action({action: 'source', path: 'synthetic-vault', selected: ['career.md']});
  assert.equal(runtime.status().step, 'resume');
  await action({action: 'resume', name: 'example-resume.pdf', data: 'synthetic'});
  await action({action: 'preferences', roles: ['Solutions Engineer'], locations: ['Example region'], sponsorship: true});
  await action({action: 'codex'});
  assert.equal(runtime.status().checks.codex.status, 'pending');
  await assert.rejects(action({action: 'complete'}), error => error.code === 'CHECKS_INCOMPLETE');
  await item.dispatcherOptions.onConnection({clientName: 'codex-test', verifiedAt: '2026-01-01T00:00:00Z'});
  await action({action: 'continue'});
  assert.equal(runtime.status().step, 'review');
  await action({action: 'complete'});
  assert.equal(runtime.status().completed, true);
  assert.equal(runtime.status().paused, true);
  const saved = JSON.parse(await readFile(path.join(item.root, '.private', 'onboarding.json'), 'utf8'));
  assert.equal(saved.paused, true);
});

test('stale or simultaneous setup writes cannot overwrite newer progress', async t => {
  const {runtime} = await fixture(t);
  const revision = runtime.status().revision;
  const result = await Promise.allSettled([runtime.setup({action: 'continue', revision}), runtime.setup({action: 'continue', revision})]);
  assert.equal(result.filter(r => r.status === 'fulfilled').length, 1);
  assert.equal(result.find(r => r.status === 'rejected').reason.code, 'REVISION_CONFLICT');
  assert.equal(runtime.status().revision, revision + 1);
});

test('missing or corrupted private inputs invalidate checks and block completion despite saved flags', async t => {
  const item = await fixture(t), {runtime, valid} = item;
  const action = data => runtime.setup({...data, revision: runtime.status().revision});
  await action({action: 'source', path: 'synthetic-vault', selected: ['career.md']});
  await action({action: 'resume', name: 'example-resume.pdf', data: 'synthetic'});
  await action({action: 'preferences', roles: ['Example Engineer'], locations: ['Example region'], sponsorship: false});
  await item.dispatcherOptions.onConnection({clientName: 'codex-test', verifiedAt: '2026-01-01T00:00:00Z'});
  valid.resume = false;
  await assert.rejects(action({action: 'complete'}), error => error.code === 'CHECKS_INCOMPLETE');
  assert.equal(runtime.status().checks.resume.status, 'error');
  valid.resume = true; valid.source = false;
  await runtime.refreshInputs();
  assert.equal(runtime.status().checks.source.status, 'error');
  await assert.rejects(action({action: 'complete'}), error => error.code === 'CHECKS_INCOMPLETE');
  assert.equal(runtime.status().completed, false);
});

test('forged gateway identities are stripped and only authenticated API requests reach Worker', async t => {
  const {runtime} = await fixture(t);
  let forwarded = false;
  const bad = request({'oai-authenticated-user-id': 'forged'}), denied = response();
  await runtime.middleware(bad, denied, () => { forwarded = true; });
  assert.equal(denied.statusCode, 401); assert.equal(forwarded, false); assert.equal(bad.headers['oai-authenticated-user-id'], undefined);
  const good = request({cookie: 'job_workspace_session=' + runtime.security.sessionToken}, 'GET', '/api/workspace');
  await runtime.middleware(good, response(), () => { forwarded = true; });
  assert.equal(forwarded, true); assert.equal(good.headers['oai-authenticated-user-id'], runtime.ownerIdentity);
});

test('the actual Cloudflare rawHeaders bridge sees only the verified gateway identity', async t => {
  const {runtime} = await fixture(t);
  const headers = {cookie: 'job_workspace_session=' + runtime.security.sessionToken, 'oai-authenticated-user-id': 'forged-map'};
  const forwarded = request(headers, 'GET', '/api/workspace');
  forwarded.rawHeaders.push('OAI-Authenticated-User-ID', 'forged-duplicate');
  await runtime.middleware(forwarded, response(), () => {});
  // The pinned Cloudflare Vite bridge constructs its Fetch headers from this
  // raw list, ignoring IncomingMessage.headers changes by themselves.
  const fetchHeaders = new Headers();
  for (let i = 0; i < forwarded.rawHeaders.length; i += 2) fetchHeaders.append(forwarded.rawHeaders[i], forwarded.rawHeaders[i + 1]);
  assert.equal(fetchHeaders.get('oai-authenticated-user-id'), runtime.ownerIdentity);
  assert.equal(fetchHeaders.get('cookie'), headers.cookie);
  const denied = request({'oai-authenticated-user-id': 'forged-map'});
  denied.rawHeaders.push('OAI-Authenticated-User-ID', 'forged-raw');
  await runtime.middleware(denied, response(), () => assert.fail('unauthorized request forwarded'));
  assert.equal(denied.rawHeaders.some(v => String(v).toLowerCase() === 'oai-authenticated-user-id'), false);
});

test('private filesystem paths are not served and MCP delegation needs the same private bearer', async t => {
  const {runtime} = await fixture(t);
  const denied = response();
  await runtime.middleware(request({}, 'GET', '/@fs/synthetic/.private/runtime.json'), denied, () => assert.fail('private path forwarded'));
  assert.equal(denied.statusCode, 403);
  const delegated = request({authorization: 'Bearer ' + runtime.security.mcpToken}, 'POST', '/mcp?workspace=1');
  let forwarded = false;
  await runtime.middleware(delegated, response(), () => { forwarded = true; });
  assert.equal(forwarded, true); assert.equal(delegated.headers['oai-authenticated-user-id'], runtime.ownerIdentity);
});

test('README links may navigate to the top-level setup document without allowing cross-site APIs or iframes', async t => {
  const {runtime} = await fixture(t);
  const headers = {accept: 'text/html', 'sec-fetch-site': 'cross-site', 'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document'};
  const page = response(); let forwarded = false;
  await runtime.middleware(request(headers, 'GET', '/setup'), page, () => { forwarded = true; });
  assert.equal(forwarded, true);
  assert.match(page.headers['set-cookie'], /HttpOnly; SameSite=Strict/);
  assert.equal(page.headers['x-frame-options'], 'DENY');
  assert.equal(page.headers['content-security-policy'], "frame-ancestors 'none'");
  for (const [url, dest] of [['/api/setup', 'document'], ['/setup', 'iframe'], ['/local/runtime.mjs', 'script']]) {
    const denied = response();
    await runtime.middleware(request({...headers, 'sec-fetch-dest': dest}, 'GET', url), denied, () => assert.fail('cross-site request forwarded'));
    assert.equal(denied.statusCode, 403);
  }
});

test('storage failure is visible without leaking backend paths or connection values', async t => {
  const {runtime} = await fixture(t, {bootstrapStorage: async () => { throw Error('synthetic private backend detail'); }});
  assert.equal(runtime.status().checks.storage.status, 'error');
  assert.equal(JSON.stringify(runtime.status()).includes('synthetic private backend detail'), false);
});

test('private root cannot be a symlink', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'workspace-symlink-test-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const target = path.join(root, 'target'); await mkdir(target);
  await symlink(target, path.join(root, '.private'));
  await assert.rejects(createLocalRuntime({root}), error => error.code === 'UNSAFE_STORAGE');
});
