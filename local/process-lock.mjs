/** One laptop server owns each private checkout, even on different ports. */
import {constants} from 'node:fs';
import {chmod, lstat, mkdir, open, rename, unlink} from 'node:fs/promises';
import {randomBytes} from 'node:crypto';
import path from 'node:path';

export class LocalLockError extends Error {
  constructor(message, code) { super(message); this.code = code; }
}

export function processIsAlive(pid, probe = process.kill) {
  try { probe(pid, 0); return true; }
  catch (error) { return error.code !== 'ESRCH'; }
}

async function lockContents(filename) {
  const info = await lstat(filename);
  if (!info.isFile() || info.isSymbolicLink() || info.size > 4096) throw new LocalLockError('The local process lock needs review.', 'LOCAL_LOCK_INVALID');
  const handle = await open(filename, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  try {
    const value = JSON.parse(await handle.readFile('utf8'));
    if (!Number.isSafeInteger(value.pid) || value.pid <= 0 || typeof value.nonce !== 'string' || !/^[a-f0-9]{32}$/.test(value.nonce) ||
      (value.childPid !== undefined && (!Number.isSafeInteger(value.childPid) || value.childPid <= 0))) throw new Error('Invalid lock');
    return value;
  } catch { throw new LocalLockError('The local process lock needs review.', 'LOCAL_LOCK_INVALID'); }
  finally { await handle.close(); }
}

async function createLock(filename, value) {
  const handle = await open(filename, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW || 0), 0o600);
  try { await handle.writeFile(JSON.stringify(value) + '\n'); await handle.sync(); }
  finally { await handle.close(); }
  await chmod(filename, 0o600);
}

async function releaseIfOwned(filename, owner) {
  try {
    const current = await lockContents(filename);
    if (current.pid === owner.pid && current.nonce === owner.nonce) await unlink(filename);
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
}

export async function acquireLocalProcessLock(root, {pid = process.pid, isAlive = processIsAlive} = {}) {
  const directory = path.join(path.resolve(root), '.private');
  await mkdir(directory, {recursive: true, mode: 0o700});
  const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new LocalLockError('Private storage must be a real directory.', 'LOCAL_LOCK_INVALID');
  await chmod(directory, 0o700);
  const filename = path.join(directory, 'local-process.lock');
  const recovery = path.join(directory, 'local-process-recovery.lock');
  const owner = {pid, nonce: randomBytes(16).toString('hex')};
  const active = value => isAlive(value.pid) || (value.childPid !== undefined && isAlive(value.childPid));
  try { await createLock(filename, owner); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const previous = await lockContents(filename);
    if (active(previous)) throw new LocalLockError('This workspace is already running. Close its local server before starting another one.', 'LOCAL_ALREADY_RUNNING');
    // Only one process can recover a stale primary lock. A simultaneous normal
    // acquire still uses O_EXCL; it can win, but can never create a second owner.
    try { await createLock(recovery, owner); }
    catch (failure) {
      if (failure.code === 'EEXIST') throw new LocalLockError('Another startup is recovering local storage. Try again shortly.', 'LOCAL_LOCK_BUSY');
      throw failure;
    }
    try {
      const current = await lockContents(filename);
      if (current.pid !== previous.pid || current.nonce !== previous.nonce || active(current)) throw new LocalLockError('This workspace is already running.', 'LOCAL_ALREADY_RUNNING');
      await unlink(filename);
      try { await createLock(filename, owner); }
      catch (failure) {
        if (failure.code === 'EEXIST') throw new LocalLockError('This workspace is already running.', 'LOCAL_ALREADY_RUNNING');
        throw failure;
      }
    } finally { await releaseIfOwned(recovery, owner); }
  }
  return {
    async trackChild(childPid) {
      if (!Number.isSafeInteger(childPid) || childPid <= 0) throw new LocalLockError('The local server could not be tracked safely.', 'LOCAL_CHILD_INVALID');
      const current = await lockContents(filename);
      if (current.pid !== owner.pid || current.nonce !== owner.nonce) throw new LocalLockError('The local process lock changed before startup.', 'LOCAL_LOCK_CHANGED');
      const temporary = filename + '.' + owner.nonce + '.tmp';
      await createLock(temporary, {...current, childPid});
      try {
        const latest = await lockContents(filename);
        if (latest.pid !== owner.pid || latest.nonce !== owner.nonce) throw new LocalLockError('The local process lock changed before startup.', 'LOCAL_LOCK_CHANGED');
        await rename(temporary, filename);
      } catch (error) { await unlink(temporary).catch(() => {}); throw error; }
    },
    release: () => releaseIfOwned(filename, owner),
  };
}
