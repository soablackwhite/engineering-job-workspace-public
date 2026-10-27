import test from 'node:test';
import assert from 'node:assert/strict';
import {pickFolder,FolderPickerError} from '../local/folder-picker.mjs';

test('macOS uses only the fixed Standard Additions dialog and preserves folder whitespace', async () => {
  const selected = await pickFolder({platform: 'darwin', env: {}, timeoutMs: 900000, execRunner: async (program, args, options) => {
    assert.equal(program, '/usr/bin/osascript'); assert.equal(args[0], '-e');
    assert.match(args[1], /choose folder with prompt/);
    assert.doesNotMatch(args[1], /System Events|Finder|do shell script/);
    assert.equal(options.shell, false); assert.equal(options.killSignal, 'SIGKILL');
    assert.ok(options.timeout > 0 && options.timeout <= 90000); assert.equal(options.maxBuffer, 16384);
    return {stdout: "/synthetic/O'Brien $(example)  \n"};
  }});
  assert.equal(selected, "/synthetic/O'Brien $(example)  ");
});

test('macOS cancellation is a null result, including the native cancel error', async () => {
  assert.equal(await pickFolder({platform: 'darwin', execRunner: async () => ({stdout: '\n'})}), null);
  assert.equal(await pickFolder({platform: 'darwin', execRunner: async () => { throw {code: 1, stderr: 'execution error: User canceled. (-128)'}; }}), null);
});

test('Windows launches fixed PowerShell in STA with UTF8 output and no shell', async () => {
  const selected = await pickFolder({platform: 'win32', env: {SystemRoot: 'C:\\Windows'}, execRunner: async (program, args, options) => {
    assert.equal(program, 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe');
    assert.ok(args.includes('-NoProfile')); assert.ok(args.includes('-STA'));
    const script = args.at(-1); assert.match(script, /System\.Windows\.Forms\.FolderBrowserDialog/);
    assert.match(script, /UTF8Encoding/); assert.match(script, /ShowNewFolderButton = \$false/);
    assert.equal(options.shell, false);
    return {stdout: 'C:\\Example\\Career Notes \r\n'};
  }});
  assert.equal(selected, 'C:\\Example\\Career Notes ');
});

test('Linux falls back to kdialog only when zenity is missing', async () => {
  const programs = [];
  const selected = await pickFolder({platform: 'linux', env: {DISPLAY: ':99'}, execRunner: async (program, args, options) => {
    programs.push(program); assert.equal(options.shell, false);
    if (program === 'zenity') { assert.deepEqual(args.slice(0, 2), ['--file-selection', '--directory']); throw {code: 'ENOENT'}; }
    assert.equal(args[0], '--getexistingdirectory'); return {stdout: '/synthetic/notes\n'};
  }});
  assert.deepEqual(programs, ['zenity', 'kdialog']); assert.equal(selected, '/synthetic/notes');
});

test('Linux cancellation does not open a second dialog', async () => {
  let calls = 0;
  assert.equal(await pickFolder({platform: 'linux', env: {WAYLAND_DISPLAY: 'example'}, execRunner: async () => { calls++; throw {code: 1}; }}), null);
  assert.equal(calls, 1);
});

test('headless or unsupported systems fail without starting a process', async () => {
  for (const options of [{platform: 'linux', env: {}}, {platform: 'unsupported', env: {}}]) {
    await assert.rejects(pickFolder({...options, execRunner: async () => assert.fail('unexpected OS command')}), error => error instanceof FolderPickerError && error.code === 'FOLDER_PICKER_UNAVAILABLE');
  }
});

test('missing desktop tools and command failures return controlled messages only', async () => {
  for (const [options, code] of [
    [{platform: 'linux', env: {DISPLAY: ':99'}, execRunner: async () => { throw {code: 'ENOENT', stderr: 'synthetic-private-stderr'}; }}, 'FOLDER_PICKER_UNAVAILABLE'],
    [{platform: 'darwin', execRunner: async () => { throw {code: 77, stdout: 'synthetic-private-path', stderr: 'synthetic-private-stderr'}; }}, 'FOLDER_PICKER_FAILED'],
    [{platform: 'linux', env: {DISPLAY: ':99'}, execRunner: async () => { throw {code: 1, stderr: 'Gtk-WARNING cannot open display: synthetic-private-value'}; }}, 'FOLDER_PICKER_UNAVAILABLE'],
  ]) {
    await assert.rejects(pickFolder(options), error => {
      assert.equal(error.code, code); assert.doesNotMatch(error.message, /synthetic-private|osascript|powershell/); return true;
    });
  }
});

test('timeouts are bounded and never expose subprocess stderr', async () => {
  await assert.rejects(pickFolder({platform: 'darwin', timeoutMs: 50, execRunner: async (_program, _args, options) => {
    assert.ok(options.timeout <= 50); assert.equal(options.killSignal, 'SIGKILL');
    throw {killed: true, signal: 'SIGKILL', stderr: 'synthetic-private-stderr'};
  }}), error => error.code === 'FOLDER_PICKER_TIMEOUT' && !error.message.includes('synthetic-private'));
});

test('unexpected, oversized or nonabsolute folder results are rejected', async () => {
  for (const stdout of ['relative/notes\n', '/synthetic/notes\0bad\n', '/'+ 'x'.repeat(4097), '/'+ 'x'.repeat(17000)]) {
    await assert.rejects(pickFolder({platform: 'darwin', execRunner: async () => ({stdout})}), error => error instanceof FolderPickerError && ['FOLDER_PICKER_INVALID', 'FOLDER_PICKER_FAILED'].includes(error.code));
  }
});
