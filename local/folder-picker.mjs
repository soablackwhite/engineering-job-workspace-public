/** Fixed native desktop dialogs. No request value becomes a command or script. */
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';

const execute = promisify(execFile);
const maximumOutput = 16 * 1024;
const maximumTimeout = 90 * 1000;
const macScript = `try
  return POSIX path of (choose folder with prompt "Choose your career notes folder")
on error number -128
  return ""
end try`;
const windowsScript = `$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Choose your career notes folder'
$dialog.ShowNewFolderButton = $false
try {
  if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine($dialog.SelectedPath)
  }
} finally { $dialog.Dispose() }`;

export class FolderPickerError extends Error {
  constructor(message, code, status = 422) { super(message); this.name = 'FolderPickerError'; this.code = code; this.status = status; }
}

function unavailable() {
  return new FolderPickerError('A desktop folder chooser is not available. Open the workspace in a desktop session, or use the folder-location option.', 'FOLDER_PICKER_UNAVAILABLE', 503);
}

/**
 * @param {{platform?:string,env?:NodeJS.ProcessEnv,execRunner?:(program:string,args:string[],options:object)=>Promise<{stdout?:string|Buffer,stderr?:string|Buffer}>,timeoutMs?:number}} options
 * @returns {Promise<string|null>}
 */
export async function pickFolder({platform = process.platform, env = process.env, execRunner = execute, timeoutMs = maximumTimeout} = {}) {
  const budget = Math.min(maximumTimeout, Math.max(1, Math.floor(Number(timeoutMs) || maximumTimeout)));
  let commands;
  if (platform === 'darwin') commands = [{program: '/usr/bin/osascript', args: ['-e', macScript]}];
  else if (platform === 'win32') commands = [{
    program: path.win32.join(env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
    args: ['-NoProfile', '-NonInteractive', '-STA', '-Command', windowsScript],
  }];
  else if (platform === 'linux') {
    if (!env.DISPLAY && !env.WAYLAND_DISPLAY) throw unavailable();
    commands = [
      {program: 'zenity', args: ['--file-selection', '--directory', '--title=Choose your career notes folder']},
      {program: 'kdialog', args: ['--getexistingdirectory', '.', '--title', 'Choose your career notes folder']},
    ];
  } else throw unavailable();
  const deadline = Date.now() + budget;
  for (const command of commands) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new FolderPickerError('Folder selection timed out. Open the chooser again when you are ready.', 'FOLDER_PICKER_TIMEOUT', 408);
    let stdout;
    try {
      const result = await execRunner(command.program, command.args, {
        shell: false, encoding: 'utf8', windowsHide: true, maxBuffer: maximumOutput,
        timeout: remaining, killSignal: 'SIGKILL', env,
      });
      stdout = result.stdout || '';
    } catch (error) {
      if (error.code === 'ENOENT' || error.code === 'ENOTDIR') continue;
      if (error.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER') throw new FolderPickerError('The folder chooser returned an unsupported response.', 'FOLDER_PICKER_FAILED');
      if (error.killed || error.code === 'ETIMEDOUT' || error.signal === 'SIGKILL') throw new FolderPickerError('Folder selection timed out. Open the chooser again when you are ready.', 'FOLDER_PICKER_TIMEOUT', 408);
      const stderr = String(error.stderr || '');
      if (platform === 'darwin' && /(?:\(-128\)|User cancel[el]ed)/i.test(stderr)) return null;
      if (platform === 'linux' && /cannot open display|could not connect to display|failed to initialize.*gtk|qt\.qpa\.plugin/i.test(stderr)) throw unavailable();
      if (platform === 'linux' && error.code === 1) return null;
      throw new FolderPickerError('The folder chooser could not open. Try again or use the folder-location option.', 'FOLDER_PICKER_FAILED');
    }
    const output = Buffer.isBuffer(stdout) ? stdout.toString('utf8') : String(stdout);
    if (Buffer.byteLength(output, 'utf8') > maximumOutput) throw new FolderPickerError('The folder chooser returned an unsupported response.', 'FOLDER_PICKER_FAILED');
    const selected = output.replace(/^\uFEFF/, '').replace(/\r?\n$/, '');
    if (!selected) return null;
    const absolute = platform === 'win32' ? path.win32.isAbsolute(selected) : path.posix.isAbsolute(selected);
    if (!absolute || selected.length > 4096 || selected.includes('\0')) throw new FolderPickerError('The selected folder could not be read. Please choose it again.', 'FOLDER_PICKER_INVALID');
    return selected;
  }
  throw unavailable();
}
