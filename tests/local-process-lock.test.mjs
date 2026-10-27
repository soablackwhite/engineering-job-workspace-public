import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, writeFile, stat, rm, symlink} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {acquireLocalProcessLock,processIsAlive} from '../local/process-lock.mjs';

async function temporary(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'workspace-lock-test-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  return root;
}

test('one active process owns a checkout across arbitrary ports', async t => {
  const root = await temporary(t);
  const first = await acquireLocalProcessLock(root, {pid: 101, isAlive: () => true});
  await assert.rejects(acquireLocalProcessLock(root, {pid: 202, isAlive: () => true}), error => error.code === 'LOCAL_ALREADY_RUNNING');
  assert.equal((await stat(path.join(root, '.private', 'local-process.lock'))).mode & 0o777, 0o600);
  await first.release();
  const second = await acquireLocalProcessLock(root, {pid: 202, isAlive: () => true});
  await second.release();
});

test('a dead process lock is recovered without process termination', async t => {
  const root = await temporary(t);
  await acquireLocalProcessLock(root, {pid: 101, isAlive: () => true});
  const recovered = await acquireLocalProcessLock(root, {pid: 202, isAlive: () => false});
  const current = JSON.parse(await readFile(path.join(root, '.private', 'local-process.lock'), 'utf8'));
  assert.equal(current.pid, 202);
  await recovered.release();
});

test('an orphaned live server keeps the checkout locked after its launcher dies', async t => {
  const root = await temporary(t);
  const first = await acquireLocalProcessLock(root, {pid: 101, isAlive: () => true});
  await first.trackChild(303);
  const current = JSON.parse(await readFile(path.join(root, '.private', 'local-process.lock'), 'utf8'));
  assert.equal(current.childPid, 303);
  await assert.rejects(acquireLocalProcessLock(root, {pid: 202, isAlive: pid => pid === 303}), error => error.code === 'LOCAL_ALREADY_RUNNING');
  const recovered = await acquireLocalProcessLock(root, {pid: 202, isAlive: () => false});
  await recovered.release();
});

test('release never removes a lock whose owner nonce changed', async t => {
  const root = await temporary(t);
  const first = await acquireLocalProcessLock(root, {pid: 101, isAlive: () => true});
  const filename = path.join(root, '.private', 'local-process.lock');
  await writeFile(filename, JSON.stringify({pid: 303, nonce: 'b'.repeat(32)}));
  await first.release();
  assert.equal(JSON.parse(await readFile(filename, 'utf8')).pid, 303);
});

test('permission-denied process probes count as alive; only ESRCH is stale', () => {
  assert.equal(processIsAlive(101, () => { throw Object.assign(Error(), {code: 'EPERM'}); }), true);
  assert.equal(processIsAlive(101, () => { throw Object.assign(Error(), {code: 'ESRCH'}); }), false);
});

test('a symlink lock is not followed or overwritten', async t => {
  const root = await temporary(t);
  const first = await acquireLocalProcessLock(root, {pid: 101, isAlive: () => true});
  await first.release();
  const target = path.join(root, 'synthetic.json'); await writeFile(target, '{}');
  await symlink(target, path.join(root, '.private', 'local-process.lock'));
  await assert.rejects(acquireLocalProcessLock(root, {pid: 202, isAlive: () => false}), error => error.code === 'LOCAL_LOCK_INVALID');
  assert.equal(await readFile(target, 'utf8'), '{}');
});
