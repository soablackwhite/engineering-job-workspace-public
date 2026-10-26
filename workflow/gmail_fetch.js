// Run in functions.exec with the supported tools object; no OAuth/token scraping.
// Invoke fetchGmail({cacheDir, plan}) with a plan from email_reconcile plan.
// Cached MIME data stays local, mode0600. Only counts are returned to the model.
async function fetchGmail({cacheDir, plan, onProgress = () => {}}) {
  const quote = s => "'" + String(s).replace(/'/g, "'\\''") + "'";
  const readJSON = async path => {
    const r = await tools.exec_command({cmd: 'python3 -c ' + quote('import pathlib,sys,json; p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text()) if p.exists() else None; d.pop("messages",None) if isinstance(d,dict) and "queries" in d else None; print(json.dumps(d))') + ' ' + quote(path), max_output_tokens: 200000});
    if (r.exit_code !== 0) throw Error('Cache read failed');
    return JSON.parse(r.output);
  };
  const writeJSON = async (path, value) => {
    // Stream bytes over a non-echoing, non-canonical PTY: HTML batches can exceed
    // the OS argv limit, and canonical terminals truncate long input lines.
    const data = JSON.stringify(value).replace(/[^\x00-\x7F]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
    const py = 'import sys,pathlib,os,json,termios; p=pathlib.Path(sys.argv[1]); n=int(sys.argv[2]); fd=sys.stdin.fileno(); old=termios.tcgetattr(fd); a=termios.tcgetattr(fd); a[3]&=~(termios.ECHO|termios.ICANON); a[6][termios.VMIN]=1; a[6][termios.VTIME]=0; termios.tcsetattr(fd,termios.TCSANOW,a); print("ready",flush=True); data=sys.stdin.buffer.read(n); termios.tcsetattr(fd,termios.TCSANOW,old); json.loads(data); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix(p.suffix+".tmp"); t.write_bytes(data); t.chmod(0o600); os.replace(t,p); print("saved",flush=True)';
    const start=await tools.exec_command({cmd:'python3 -c '+quote(py)+' '+quote(path)+' '+data.length,tty:true,yield_time_ms:1000,max_output_tokens:100});
    if(!start.session_id || !start.output.includes('ready'))throw Error('Cache writer did not become ready');
    let r=await tools.write_stdin({session_id:start.session_id,chars:data,yield_time_ms:1000,max_output_tokens:100});
    while(r.session_id)r=await tools.write_stdin({session_id:r.session_id,chars:'',yield_time_ms:1000,max_output_tokens:100});
    if(r.exit_code !== 0) throw Error('Atomic cache write failed');
  };
  const profile = await tools.mcp__codex_apps__gmail_get_profile({});
  if(profile.isError || profile.structuredContent?.email !== plan.account) throw Error('Gmail account mismatch');
  const runPath=cacheDir+'/ids-run-'+String(plan.through).replace(/[^0-9]/g,'')+'.json';
  let run=await readJSON(runPath);
  if(!run) run={...plan,queries:plan.queries.map(q=>({...q,pages:[],complete:false})),messages:[]};
  if(run.account !== plan.account || run.query_version !== plan.query_version || JSON.stringify(run.queries.map(q=>q.query))!==JSON.stringify(plan.queries.map(q=>q.query))) throw Error('Existing run has different query/account/version');
  for(const lane of run.queries) {
    while(!lane.complete) {
      const token=lane.pages.at(-1)?.next_page_token;
      const r=await tools.mcp__codex_apps__gmail_search_email_ids({query:lane.query,max_results:500,...(token?{next_page_token:token}:{})});
      const page=r.structuredContent;
      if(r.isError || !Array.isArray(page?.message_ids)) throw Error('Gmail ID search failed; saved run is resumable');
      if(page.next_page_token && lane.pages.some(p=>p.next_page_token===page.next_page_token)) throw Error('Repeated Gmail pagination token');
      lane.pages.push({ids:page.message_ids,next_page_token:page.next_page_token||null});
      lane.complete=!page.next_page_token;
      await writeJSON(runPath,run);
      onProgress({phase:'enumerating',pages:lane.pages.length,ids:lane.pages.reduce((n,p)=>n+p.ids.length,0)});
    }
  }
  const ids=[...new Set(run.queries.flatMap(q=>q.pages.flatMap(p=>p.ids)))];
  // Index lists cache filenames only; never reload the whole historical mailbox
  // into the agent context or a prompt. Cache payloads are assembled locally below.
  const indexPath=cacheDir+'/message-index.json';
  const index=(await readJSON(indexPath))||{};
  const missing=ids.filter(id=>!index[id]);let readCount=0;
  const parts=(p,kind)=>p.mime_type===kind?(p.body?.content||''):(p.parts||[]).map(x=>parts(x,kind)).join('\n');
  for(let i=0;i<missing.length;i+=50) {
    const batch=missing.slice(i,i+50);
    const r=await tools.mcp__codex_apps__gmail_batch_read_email({message_ids:batch});
    if(r.isError || !Array.isArray(r.structuredContent?.responses)) throw Error('Batch read failed');
    const records=[];
    for(const raw of r.structuredContent.responses) {
      if(!raw.id || !raw.thread_id || !raw.payload || !raw.internal_date) throw Error('Incomplete message; do not checkpoint');
      const h=Object.fromEntries((raw.payload.headers||[]).map(x=>[x.name.toLowerCase(),x.value]));
      records.push({id:raw.id,thread_id:raw.thread_id,date:new Date(Number(raw.internal_date)).toISOString(),subject:h.subject||'',from:h.from||'',text:parts(raw.payload,'text/plain'),html:parts(raw.payload,'text/html')});
    }
    if(batch.some(id=>!records.some(m=>m.id===id))) throw Error('Batch omitted requested message; retry safely');
    const filename='batch-'+Date.now()+'-'+i+'.json';
    await writeJSON(cacheDir+'/'+filename,records);
    for(const m of records) index[m.id]=filename;
    await writeJSON(indexPath,index);readCount+=records.length;
    onProgress({phase:'reading',newReads:readCount,missing:missing.length,total:ids.length});
  }
  const assemble='import json,pathlib,sys; root=pathlib.Path(sys.argv[1]); p=pathlib.Path(sys.argv[2]); r=json.loads(p.read_text()); idx=json.loads((root/"message-index.json").read_text()); ids={i for q in r["queries"] for x in q["pages"] for i in x["ids"]}; records={m["id"]:m for f in set(idx[i] for i in ids) for m in json.loads((root/f).read_text())}; r["messages"]=[records[i] for i in sorted(ids)]; t=p.with_suffix(".tmp"); t.write_text(json.dumps(r)); t.chmod(0o600); t.replace(p); print(len(ids))';
  const r=await tools.exec_command({cmd:'python3 -c '+quote(assemble)+' '+quote(cacheDir)+' '+quote(runPath),max_output_tokens:100});
  if(r.exit_code!==0)throw Error('Manifest assembly failed');
  const fullMailbox=plan.queries.length===1 && /^in:anywhere(?: after:\d+)? before:\d+$/.test(plan.queries[0].query);
  return {path:runPath,uniqueMessages:ids.length,newReads:readCount,cachedReads:ids.length-readCount,pages:run.queries.reduce((n,q)=>n+q.pages.length,0),coverage:fullMailbox?'All mailbox IDs and MIME bodies within query date bounds; classification review remains separate':'All pages of configured queries; heuristic mailbox coverage'};
}
