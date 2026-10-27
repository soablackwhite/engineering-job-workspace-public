'use client';

import {useEffect, useRef, useState, type DragEvent, type FormEvent} from 'react';
import {ArrowLeft, ArrowRight, Check, CheckCheck, ChevronDown, Code2, Copy, FileCheck2, FileText, FolderOpen, HardDrive, LoaderCircle, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, Upload, X} from 'lucide-react';
import './onboarding.css';

type Step = 'welcome' | 'sources' | 'resume' | 'preferences' | 'codex' | 'review' | 'complete';
type CheckState = {status: 'ready' | 'pending' | 'error'; detail?: string};
type SetupState = {
  mode: 'local'; revision: number; step: Step; completed: boolean; csrfToken: string;
  checks: Record<'storage' | 'source' | 'resume' | 'codex', CheckState>;
  source?: {label?: string; path?: string; noteCount?: number; selectedCount?: number; selected?: string[]; notes?: {path: string; title?: string}[]};
  resume?: {name: string; sha256?: string};
  preferences?: {roles: string[]; locations: string[]; sponsorship: boolean; salary?: number};
  codex?: {command?: string; status?: string};
};
const steps: {key: Step; title: string; detail: string}[] = [
  {key: 'welcome', title: 'A fresh start', detail: 'Make it yours'},
  {key: 'sources', title: 'Your knowledge', detail: 'Connect selected notes'},
  {key: 'resume', title: 'Your CV', detail: 'Add your current CV'},
  {key: 'preferences', title: 'Your next role', detail: 'Set a little direction'},
  {key: 'codex', title: 'Connect Codex', detail: 'Bring your copilot in'},
  {key: 'review', title: 'Ready to go', detail: 'Check the connections'},
];
const roles = ['Forward deployed engineer', 'Solutions engineer', 'AI engineer', 'Software engineer', 'Product engineer'];
const places = ['United States', 'United Kingdom', 'Europe', 'Remote'];

function toggle(list: string[], value: string) { return list.includes(value) ? list.filter(item => item !== value) : [...list, value]; }
function responseObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The workspace returned an unexpected response. Please try again.');
  return value as Record<string, unknown>;
}
function setupResponse(value: Record<string, unknown>): SetupState {
  const checks = responseObject(value.checks);
  if (value.mode !== 'local' || typeof value.revision !== 'number' || typeof value.csrfToken !== 'string' || typeof value.completed !== 'boolean' || ![...steps.map(step => step.key), 'complete'].includes(value.step as Step)) throw new Error('The workspace setup could not be loaded. Please try again.');
  for (const key of ['storage', 'source', 'resume', 'codex']) {
    const check = responseObject(checks[key]);
    if (!['ready', 'pending', 'error'].includes(String(check.status))) throw new Error('A workspace check returned an unexpected result. Please try again.');
  }
  return value as unknown as SetupState;
}
function fileData(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result.split(',', 2)[1]) : reject(new Error('This file could not be read.'));
    reader.onerror = () => reject(new Error('This file could not be read. Try choosing it again.'));
    reader.readAsDataURL(file);
  });
}

export default function SetupPage() {
  const [state, setState] = useState<SetupState | null>(null);
  const [loading, setLoading] = useState(true), [unavailable, setUnavailable] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [copied, setCopied] = useState(false), [promptCopied, setPromptCopied] = useState(false), [dragging, setDragging] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [sourcePath, setSourcePath] = useState(''), [selectedNotes, setSelectedNotes] = useState<string[]>([]), [notesQuery, setNotesQuery] = useState('');
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [preferenceQuestion, setPreferenceQuestion] = useState(0), [selectedRoles, setSelectedRoles] = useState<string[]>([]), [selectedPlaces, setSelectedPlaces] = useState<string[]>([]);
  const [customRole, setCustomRole] = useState(''), [customPlace, setCustomPlace] = useState(''), [sponsorship, setSponsorship] = useState<boolean | null>(null), [salary, setSalary] = useState('');
  const inputRef = useRef<HTMLInputElement>(null), titleRef = useRef<HTMLHeadingElement>(null);
  const hydrated = useRef(false);

  function accept(next: SetupState) {
    setState(next);
    if (!hydrated.current) {
      setSourcePath(next.source?.path || ''); setSelectedNotes(next.source?.selected || []);
      setSelectedRoles(next.preferences?.roles || []); setSelectedPlaces(next.preferences?.locations || []);
      setSponsorship(next.preferences ? next.preferences.sponsorship : null); setSalary(next.preferences?.salary ? String(next.preferences.salary) : '');
      hydrated.current = true;
    }
  }
  async function refresh(quiet = false) {
    if (!quiet) setError('');
    try {
      const response = await fetch('/api/setup', {cache: 'no-store'});
      if (response.status === 401) {setSessionExpired(true); throw new Error('Your local server restarted. Reload this page to reconnect; your saved progress is safe.');}
      if (response.status === 404) {setUnavailable(true); return;}
      const data = responseObject(await response.json());
      if (!response.ok || data.mode !== 'local') throw new Error(typeof data.error === 'string' ? data.error : 'Your local workspace is not responding. Keep the local server running, then try again.');
      accept(setupResponse(data)); setUnavailable(false); setSessionExpired(false);
    } catch (problem) {if (!quiet) setError(problem instanceof Error ? problem.message : 'Could not load your setup.');}
    finally {setLoading(false);}
  }
  async function send(action: string, details: Record<string, unknown> = {}) {
    if (!state || busy) return null;
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/setup', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Workspace-CSRF': state.csrfToken}, body: JSON.stringify({action, revision: state.revision, ...details})});
      const data = responseObject(await response.json());
      if (!response.ok) {
        if (response.status === 409) {await refresh(true); throw new Error('Your setup changed in another tab. The latest saved step is loaded.');}
        throw new Error(typeof data.error === 'string' ? data.error : 'That step could not be saved. Please try again.');
      }
      const next = setupResponse(data);
      accept(next);
      return next;
    } catch (problem) {setError(problem instanceof Error ? problem.message : 'That step could not be saved.'); return null;}
    finally {setBusy(false);}
  }
  useEffect(() => {void refresh();}, []);
  useEffect(() => {titleRef.current?.focus({preventScroll: true}); setCopied(false); setPromptCopied(false);}, [state?.step, preferenceQuestion]);
  useEffect(() => {
    if (state?.step !== 'codex' || state.checks.codex.status === 'ready') return;
    const interval = setInterval(() => {void refresh(true);}, 3500);
    return () => clearInterval(interval);
  }, [state?.step, state?.checks.codex.status]);

  const activeStep = state?.step || 'welcome';
  const stepIndex = activeStep === 'complete' ? steps.length : Math.max(0, steps.findIndex(item => item.key === activeStep));
  const allReady = !!state && Object.values(state.checks).every(check => check.status === 'ready');
  const complete = !!state?.completed && allReady;
  const visibleNotes = (state?.source?.notes || []).filter(note => `${note.title || ''} ${note.path}`.toLowerCase().includes(notesQuery.toLowerCase()));

  function chooseResume(file?: File) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf')) {setError('Choose a PDF copy of your CV.'); return;}
    if (file.size > 10 * 1024 * 1024) {setError('Choose a PDF smaller than 10 MB.'); return;}
    if (file.size === 0) {setError('That PDF is empty. Choose another file.'); return;}
    setError(''); setResumeFile(file);
  }
  function dropped(event: DragEvent<HTMLDivElement>) {event.preventDefault(); setDragging(false); chooseResume(event.dataTransfer.files[0]);}
  async function uploadResume() {
    if (!resumeFile) {await send('continue'); return;}
    setError('');
    try {const data = await fileData(resumeFile); await send('resume', {name: resumeFile.name, data});}
    catch (problem) {setError(problem instanceof Error ? problem.message : 'This PDF could not be read.');}
  }
  function addCustom(kind: 'role' | 'place') {
    const text = (kind === 'role' ? customRole : customPlace).trim();
    if (!text) return;
    if (kind === 'role') {setSelectedRoles(list => list.includes(text) ? list : [...list, text]); setCustomRole('');}
    else {setSelectedPlaces(list => list.includes(text) ? list : [...list, text]); setCustomPlace('');}
  }
  async function nextPreferences() {
    if (preferenceQuestion < 2) {setPreferenceQuestion(question => question + 1); return;}
    await send('preferences', {roles: selectedRoles, locations: selectedPlaces, sponsorship, ...(salary.trim() ? {salary: Number(salary)} : {})});
  }
  async function copyCommand() {
    try {await navigator.clipboard.writeText(state?.codex?.command || ''); setCopied(true);}
    catch {setError('Select and copy the command below, then run it in your terminal.');}
  }
  async function copyPrompt() {
    try {await navigator.clipboard.writeText('Call the job-workspace workspace_status tool.'); setPromptCopied(true);}
    catch {setError('Select and copy the prompt below, then send it in Codex.');}
  }
  async function back(step?: Step) {
    if (step) {if (step === 'preferences') setPreferenceQuestion(0); await send('back', {step}); return;}
    if (activeStep === 'preferences' && preferenceQuestion > 0) {setPreferenceQuestion(question => question - 1); return;}
    await send('back');
  }
  const proceed = (label: string, action: () => void, disabled = false) => <button type="button" className="setup-next" onClick={action} disabled={disabled || busy}>{busy ? <><LoaderCircle size={18} className="setup-spin"/> Saving…</> : <>{label}<ArrowRight size={18}/></>}</button>;
  const title = (eyebrow: string, heading: string, description: string) => <div className="setup-question-heading"><div className="setup-eyebrow">{eyebrow}</div><h1 ref={titleRef} tabIndex={-1}>{heading}</h1><p>{description}</p></div>;

  return <div className="setup-shell">
    <header className="setup-header"><a href="/" className="setup-brand" aria-label="Job workspace home"><span className="setup-brand-mark"><span/><span/></span>Job workspace</a><span className="setup-local"><span/>On your laptop<LockKeyhole size={13}/></span></header>
    <div className="setup-layout">
      <aside className="setup-sidebar" aria-label="Setup progress">
        <div className="setup-sidebar-top"><div className="setup-eyebrow">A LITTLE SETUP. A CLEARER SEARCH.</div><h2>Your workspace,<br/>your way.</h2><p>A few small steps.<br/>No accounts to untangle.</p></div>
        <ol className="setup-steps">{steps.map((item, index) => <li key={item.key} className={index < stepIndex ? 'is-done' : index === stepIndex ? 'is-current' : ''} aria-current={index === stepIndex ? 'step' : undefined}><span className="setup-step-number">{index < stepIndex ? <Check size={14}/> : index + 1}</span><div><span>{item.title}</span><small>{item.detail}</small></div>{index === stepIndex && <span className="setup-current-dot"/>}</li>)}</ol>
        <div className="setup-sidebar-note"><ShieldCheck size={20}/><div><strong>Private by design</strong><p>Your CV and selected sources stay out of the public repository. You choose what Codex can read.</p></div></div>
      </aside>
      <main className="setup-main" id="setup-main">
        <div className="setup-progress-label"><span>{complete ? 'ALL SET' : `STEP ${Math.min(stepIndex + 1, steps.length)} OF ${steps.length}`}</span><span>{complete ? 'Every connection checked' : 'Completed steps are saved'}</span></div>
        <div className="setup-mobile-progress" aria-hidden="true">{steps.map((item, index) => <span key={item.key} className={index <= stepIndex ? 'filled' : ''}/>)}</div>
        <section key={`${activeStep}-${preferenceQuestion}`} className={`setup-card ${activeStep === 'welcome' ? 'setup-welcome-card' : ''}`} aria-busy={loading || busy}>
          {loading ? <div className="setup-loading" role="status"><LoaderCircle className="setup-spin" size={26}/><h1>Preparing your workspace…</h1><p>Picking up where you left off.</p></div> : unavailable ? <>
            {title('LET’S KEEP IT LOCAL', 'Start setup on your laptop.', 'This guided setup is available when the local workspace is running.')}
            <div className="setup-command"><code>npm run local</code></div><p className="setup-help">Run this in the project folder, then open the address shown in your terminal.</p><div className="setup-actions"><a href="/" className="setup-text-button"><ArrowLeft size={16}/>Back to workspace</a><button className="setup-next" onClick={() => void refresh()}>Try again<RefreshCw size={17}/></button></div>
          </> : !state ? <>
            {title('LET’S GET CONNECTED', 'Your workspace needs a moment.', 'Keep the local server running, then try connecting again.')}<div className="setup-actions">{proceed(sessionExpired ? 'Reload workspace' : 'Try again', () => sessionExpired ? window.location.reload() : void refresh())}</div>
          </> : complete ? <div className="setup-complete">
            <div className="setup-success-art"><CheckCheck size={34}/><span/><span/></div>
            {title('YOUR NEXT CHAPTER STARTS HERE', 'You’re fully onboarded.', 'Your selected sources are connected, your CV is saved, and Codex has checked in.')}
            <div className="setup-complete-checks"><span><Check size={15}/>Local workspace</span><span><Check size={15}/>Selected notes</span><span><Check size={15}/>Saved CV</span><span><Check size={15}/>Codex connected</span></div>
            <a className="setup-next" href="/">Open workspace<ArrowRight size={18}/></a><button type="button" className="setup-review-again" onClick={() => void back('review')} disabled={busy}>Review setup</button><p className="setup-finish-note"><LockKeyhole size={13}/>Applications stay paused until you review and authorize them.</p>
          </div> : activeStep === 'welcome' ? <>
            <div className="setup-welcome-art" aria-hidden="true"><span className="setup-art-orbit"/><div className="setup-art-note"><FileText size={24}/><i/><i/></div><div className="setup-art-core"><Sparkles size={30}/></div><div className="setup-art-cv"><FileCheck2 size={24}/><i/><i/></div><span className="setup-art-twinkle">✦</span></div>
            {title('A FRESH START', 'A good next move starts here.', 'Bring your knowledge, add your CV, and connect Codex. We’ll guide you through it, one step at a time.')}
            <div className="setup-welcome-promises"><span><HardDrive size={16}/>Runs on your laptop</span><span><LockKeyhole size={16}/>No API key needed</span></div>
            <div className="setup-actions">{proceed('Make it yours', () => void send('continue'))}<span className="setup-gentle-note">You can leave and come back.</span></div>
          </> : activeStep === 'sources' ? <>
            {title('YOUR KNOWLEDGE', state.source?.notes?.length ? 'Choose the notes that tell your story.' : 'Where do your career notes live?', state.source?.notes?.length ? 'Select only the notes you want Codex to use. Your other files stay outside this workspace.' : 'Connect an Obsidian vault or a folder of Markdown notes. You’ll choose exactly which notes to include next.')}
            {!state.source?.notes?.length ? <form onSubmit={(event: FormEvent) => {event.preventDefault(); setSelectedNotes([]); setNotesQuery(''); void send('source', {path: sourcePath.trim()});}}><label htmlFor="source-path" className="setup-label">Folder location</label><div className="setup-path-input"><FolderOpen size={19}/><input id="source-path" placeholder="Paste the path to your notes folder" value={sourcePath} onChange={event => setSourcePath(event.target.value)} autoComplete="off" spellCheck={false} required/></div><p className="setup-help">In Obsidian, find your vault folder from the vault switcher. Connecting a folder doesn’t select all its notes.</p><div className="setup-actions"><button type="button" className="setup-text-button" onClick={() => void back()} disabled={busy}><ArrowLeft size={16}/>Back</button><button type="submit" className="setup-next" disabled={!sourcePath.trim() || busy}>{busy ? <LoaderCircle size={18} className="setup-spin"/> : <>Find notes<ArrowRight size={18}/></>}</button></div></form> : <>
              <div className="setup-source-summary"><span className="setup-small-icon"><FolderOpen size={19}/></span><div><strong>{state.source.label || 'Your notes folder'}</strong><span>{state.source.noteCount || state.source.notes.length} notes found</span></div><button className="setup-inline-link" onClick={() => {setState({...state, source: {...state.source, notes: []}}); setSelectedNotes([]); setNotesQuery('');}}>Change folder</button></div>
              <div className="setup-note-toolbar"><input aria-label="Find a note" value={notesQuery} onChange={event => setNotesQuery(event.target.value)} placeholder="Find a note…"/><button type="button" onClick={() => setSelectedNotes(selectedNotes.length === state.source?.notes?.length ? [] : state.source?.notes?.map(note => note.path) || [])}>{selectedNotes.length === state.source.notes.length ? 'Clear selection' : 'Select all'}</button></div>
              <div className="setup-note-list">{visibleNotes.length ? visibleNotes.map(note => <label className="setup-note" key={note.path}><input type="checkbox" checked={selectedNotes.includes(note.path)} onChange={() => setSelectedNotes(list => toggle(list, note.path))}/><FileText size={18}/><span><strong>{note.title || note.path.split('/').pop()}</strong><small>{note.path}</small></span></label>) : <p className="setup-note-empty">No notes match that search.</p>}</div>
              <p className="setup-help"><LockKeyhole size={13}/>Only selected notes are copied into your private workspace snapshot.</p><div className="setup-actions"><button className="setup-text-button" onClick={() => void back()} disabled={busy}><ArrowLeft size={16}/>Back</button>{proceed(`Use ${selectedNotes.length} ${selectedNotes.length === 1 ? 'note' : 'notes'}`, () => void send('source', {path: sourcePath || state.source?.path, selected: selectedNotes}), !selectedNotes.length)}</div>
            </>}
          </> : activeStep === 'resume' ? <>
            {title('YOUR CV', 'Which CV should we use?', 'Add the PDF you want employers to receive. We’ll keep this copy fixed until you choose to replace it.')}
            <input ref={inputRef} type="file" accept="application/pdf,.pdf" className="setup-file-input" aria-label="Choose your CV PDF" onChange={event => chooseResume(event.target.files?.[0])}/>
            <div className={`setup-dropzone ${dragging ? 'is-dragging' : ''} ${resumeFile || state.resume ? 'has-file' : ''}`} onDragOver={event => {event.preventDefault(); setDragging(true);}} onDragLeave={() => setDragging(false)} onDrop={dropped}>
              {resumeFile || state.resume ? <><span className="setup-file-symbol"><FileCheck2 size={27}/></span><h2>{resumeFile?.name || state.resume?.name}</h2><p>{resumeFile ? `${(resumeFile.size / 1024 / 1024).toFixed(1)} MB · PDF document` : 'Saved privately on this laptop'}</p><button type="button" className="setup-outline-button" onClick={() => inputRef.current?.click()}>Choose another PDF</button></> : <><span className="setup-upload-symbol"><Upload size={26}/></span><h2>Drop your CV here</h2><p>A PDF file, up to 10 MB</p><button type="button" className="setup-outline-button" onClick={() => inputRef.current?.click()}>Choose a PDF</button></>}
            </div>
            <p className="setup-help"><LockKeyhole size={13}/>Your CV stays out of Git. Nothing is sent to an employer during setup.</p><div className="setup-actions"><button className="setup-text-button" onClick={() => void back()} disabled={busy}><ArrowLeft size={16}/>Back</button>{proceed(resumeFile ? 'Use this CV' : 'Continue', () => void uploadResume(), !resumeFile && !state.resume)}</div>
          </> : activeStep === 'preferences' ? <>
            <div className="setup-mini-progress" aria-label={`Preference question ${preferenceQuestion + 1} of 3`}>{[0, 1, 2].map(index => <span key={index} className={index <= preferenceQuestion ? 'filled' : ''}/>)}</div>
            {preferenceQuestion === 0 ? <>
              {title('YOUR NEXT ROLE · 1 OF 3', 'What kind of work is next?', 'Pick a few role families. You can fine-tune your search later.')}
              <div className="setup-chips">{[...roles, ...selectedRoles.filter(role => !roles.includes(role))].map(role => <button key={role} className={selectedRoles.includes(role) ? 'is-selected' : ''} aria-pressed={selectedRoles.includes(role)} onClick={() => setSelectedRoles(list => toggle(list, role))}>{selectedRoles.includes(role) && <Check size={15}/>} {role}</button>)}</div>
              <form className="setup-add-choice" onSubmit={event => {event.preventDefault(); addCustom('role');}}><input aria-label="Another role" value={customRole} onChange={event => setCustomRole(event.target.value)} maxLength={100} placeholder="Something else? Add a role"/><button type="submit" disabled={!customRole.trim()}>Add</button></form>
            </> : preferenceQuestion === 1 ? <>
              {title('YOUR NEXT ROLE · 2 OF 3', 'Where would you like to work?', 'Choose countries, cities, or remote work. Add as many as you need.')}
              <div className="setup-chips">{[...places, ...selectedPlaces.filter(place => !places.includes(place))].map(place => <button key={place} className={selectedPlaces.includes(place) ? 'is-selected' : ''} aria-pressed={selectedPlaces.includes(place)} onClick={() => setSelectedPlaces(list => toggle(list, place))}>{selectedPlaces.includes(place) && <Check size={15}/>} {place}</button>)}</div>
              <form className="setup-add-choice" onSubmit={event => {event.preventDefault(); addCustom('place');}}><input aria-label="Another location" value={customPlace} onChange={event => setCustomPlace(event.target.value)} maxLength={100} placeholder="Add a city or country"/><button type="submit" disabled={!customPlace.trim()}>Add</button></form>
            </> : <>
              {title('YOUR NEXT ROLE · 3 OF 3', 'Will you need visa sponsorship?', 'We’ll use this to check opportunities. Your answer can be updated later.')}
              <div className="setup-answer-options"><button className={sponsorship === true ? 'is-selected' : ''} aria-pressed={sponsorship === true} onClick={() => setSponsorship(true)}><span>Yes, I’ll need sponsorship</span><i>{sponsorship === true && <Check size={15}/>}</i></button><button className={sponsorship === false ? 'is-selected' : ''} aria-pressed={sponsorship === false} onClick={() => setSponsorship(false)}><span>No sponsorship needed</span><i>{sponsorship === false && <Check size={15}/>}</i></button></div>
              <details className="setup-optional"><summary>Add a salary preference <span>Optional</span><ChevronDown size={16}/></summary><label htmlFor="salary" className="setup-label">Annual base salary target · USD</label><div className="setup-salary-input"><span>$</span><input id="salary" type="number" min="0" max="10000000" step="1000" value={salary} onChange={event => setSalary(event.target.value)} placeholder="Your minimum target"/></div><p className="setup-help">A search preference, separate from any employer offer.</p></details>
            </>}
            <div className="setup-actions"><button className="setup-text-button" onClick={() => void back()} disabled={busy}><ArrowLeft size={16}/>Back</button>{proceed('Continue', () => void nextPreferences(), preferenceQuestion === 0 ? !selectedRoles.length : preferenceQuestion === 1 ? !selectedPlaces.length : sponsorship === null || (!!salary && (Number(salary) < 0 || !Number.isFinite(Number(salary)))))}</div>
          </> : activeStep === 'codex' ? <>
            {title('YOUR COPILOT', state.checks.codex.status === 'ready' ? 'Codex is connected.' : 'Bring Codex into your workspace.', state.checks.codex.status === 'ready' ? 'The connection checked in successfully. Codex can now access the sources you selected.' : 'Connect your existing Codex installation. There’s no new account or API key to manage.')}
            <div className={`setup-codex-status ${state.checks.codex.status === 'ready' ? 'is-connected' : ''}`}><span className="setup-codex-mark"><Code2 size={27}/></span><div><strong>Codex</strong><span>{state.checks.codex.status === 'ready' ? 'Connection verified' : state.codex?.command ? 'Waiting for the first connection' : 'Ready to connect'}</span></div>{state.checks.codex.status === 'ready' ? <span className="setup-verified"><Check size={15}/>Connected</span> : <span className="setup-waiting-dot"/>}</div>
            {state.codex?.command ? <><p className="setup-instruction">Run this once in your terminal. Reopen Codex so it can load the connection.</p><div className="setup-command"><code>{state.codex.command}</code><button type="button" onClick={() => void copyCommand()} aria-label={copied ? 'Command copied' : 'Copy connection command'}>{copied ? <Check size={17}/> : <Copy size={17}/>}</button></div><p className="setup-help">{copied ? 'Copied. Paste it into your terminal to connect.' : 'This adds the workspace connection. It does not give permission to apply.'}</p><p className="setup-instruction" style={{marginTop:20}}>Then send this prompt in Codex:</p><div className="setup-command"><code>Call the job-workspace workspace_status tool.</code><button type="button" onClick={() => void copyPrompt()} aria-label={promptCopied ? 'Verification prompt copied' : 'Copy verification prompt'}>{promptCopied ? <Check size={17}/> : <Copy size={17}/>}</button></div><div className="setup-connection-note" role="status">{state.checks.codex.status === 'ready' ? <><Check size={16}/>The connection is working.</> : <><LoaderCircle size={16} className="setup-spin"/>Checking for Codex…<button type="button" onClick={() => void refresh()}>Check now</button></>}</div></> : <div className="setup-codex-explainer"><ShieldCheck size={19}/><p>Codex will receive access to this workspace and your selected notes. You keep control of each application.</p></div>}
            <div className="setup-actions"><button className="setup-text-button" onClick={() => void back()} disabled={busy}><ArrowLeft size={16}/>Back</button>{state.checks.codex.status === 'ready' ? proceed('Continue', () => void send('continue')) : state.codex?.command ? <button type="button" className="setup-outline-button" onClick={() => void copyCommand()}>{copied ? <Check size={16}/> : <Copy size={16}/>} {copied ? 'Copied' : 'Copy command'}</button> : proceed('Get connection command', () => void send('codex'))}</div>
          </> : <>
            {title('ONE LAST LOOK', 'Everything in its place.', 'We’ve checked the essentials. Your search stays in your hands.')}
            <div className="setup-review-list">{[
              {key: 'storage' as const, step: undefined, icon: HardDrive, title: 'Your local workspace', detail: 'Private storage is ready on this laptop'},
              {key: 'source' as const, step: 'sources' as Step, icon: FolderOpen, title: state.source?.label || 'Your selected notes', detail: `${state.source?.selectedCount || state.source?.selected?.length || 0} notes selected for Codex`},
              {key: 'resume' as const, step: 'resume' as Step, icon: FileCheck2, title: state.resume?.name || 'Your CV', detail: 'A fixed copy, saved privately'},
              {key: 'codex' as const, step: 'codex' as Step, icon: Code2, title: 'Codex', detail: 'Connection verified'},
            ].map(item => <div className="setup-review-row" key={item.key}><span className="setup-small-icon"><item.icon size={19}/></span><div><strong>{item.title}</strong><span>{state.checks[item.key].detail || item.detail}</span></div>{item.step && <button className="setup-inline-link" onClick={() => void back(item.step)} disabled={busy} aria-label={`Edit ${item.title}`}>Edit</button>}<span className={`setup-check-badge ${state.checks[item.key].status === 'ready' ? 'ready' : ''}`}>{state.checks[item.key].status === 'ready' ? <Check size={15}/> : <RefreshCw size={14}/>}</span></div>)}</div>
            <div className="setup-review-direction"><span>LOOKING AHEAD</span><button className="setup-inline-link" onClick={() => void back('preferences')} disabled={busy}>Edit</button><p>{state.preferences?.roles.join(' · ') || 'Your chosen roles'}</p><small>{state.preferences?.locations.join(' · ') || 'Your chosen locations'}{state.preferences ? ` · ${state.preferences.sponsorship ? 'Sponsorship needed' : 'No sponsorship needed'}` : ''}</small></div>
            {!allReady && <p className="setup-attention">A connection needs attention before setup can finish. Go back to check it, or refresh the checks.</p>}
            <div className="setup-actions"><button className="setup-text-button" onClick={() => void back()} disabled={busy}><ArrowLeft size={16}/>Back</button>{proceed('Finish setup', () => void send('complete'), !allReady)}</div><p className="setup-finish-note"><LockKeyhole size={13}/>Setup doesn’t start applications. Review and authorize them separately.</p>
          </>}
          {state?.checks.storage.status === 'error' && <div className="setup-error" role="alert"><span>{state.checks.storage.detail || 'Local storage needs attention before setup can finish. Check the local server, then reload this page.'}</span></div>}
          {error && <div className="setup-error" role="alert"><span>{error}</span>{sessionExpired && <button type="button" onClick={() => window.location.reload()}>Reload</button>}<button type="button" aria-label="Dismiss message" onClick={() => setError('')}><X size={16}/></button></div>}
        </section>
        <footer className="setup-footer"><LockKeyhole size={13}/><span>Private setup. Your pace. Your next chapter.</span><a href="/">Workspace home<ArrowRight size={13}/></a></footer>
      </main>
    </div>
  </div>;
}
