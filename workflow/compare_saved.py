"""Offline runner replay on frozen saved descriptions; not a model benchmark.

Run this file by absolute path with PYTHONPATH pointing at the immutable baseline
or current site. Callbacks return the same curator-supplied partial observations;
no model, browser, Gmail, CRM or provider credentials are used.
"""
import argparse
import copy
from datetime import datetime,timezone
import inspect
import json
from pathlib import Path
import socket
import tempfile
import time
from unittest.mock import patch
from workflow.engine import Pipeline,LocalStore,sha
from workflow.runner import prepare_jobs
from workflow.tests.fixtures import candidate


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--corpus',required=True);parser.add_argument('--output',required=True);parser.add_argument('--label',required=True)
    args=parser.parse_args();corpus=json.loads(Path(args.corpus).read_text())
    hardened='reviewer' in inspect.signature(prepare_jobs).parameters
    with tempfile.TemporaryDirectory() as tmp, patch.object(socket.socket,'connect',side_effect=AssertionError('External network blocked')):
        root=Path(tmp);p=Pipeline(LocalStore(root/'state.sqlite'));p.candidate(candidate(root,years=2));p.pause(False)
        ids=p.ingest([{k:j[k] for k in ('id','company','title','url','description','checked_at')} for j in corpus['jobs']])
        rows={j['id']:j for j in corpus['jobs']};counts={};phases=[]
        def output(job):
            value=copy.deepcopy(rows[job['id']]['extraction'])
            # Replay clock, NOT a live freshness check. Fixed for all phases.
            for observation in value['observations'].values():observation['checked_at']=replay_clock
            value['capture_review']=dict(snapshot_hash=job['snapshots'][-1]['hash'],extraction_hash=sha(value),complete=True,reviewer='offline-curated-second-pass')
            return value
        def count(stage,job,ctx):
            item=counts.setdefault(stage,{'calls':0,'input_characters':0});item['calls']+=1
            item['input_characters']+=len(json.dumps({'job':job,'context':ctx},ensure_ascii=False))
        def extract(job,ctx):count('extract',job,ctx);return output(job)
        def review(job,ctx):count('review',job,ctx);return output(job)
        def draft(job,ctx):
            count('draft',job,ctx)
            raise AssertionError('No job in this corpus has verified eligibility; draft must not run')
        replay_clock=datetime.now(timezone.utc).isoformat()
        for phase in ('cold','unchanged','unrelated_note_edit','python_fact_correction'):
            if phase in ('unrelated_note_edit','python_fact_correction'):
                file=root/'vault/facts.md';text=file.read_text()
                file.write_text(text+'\n\nWriting preference: concise answers.' if phase=='unrelated_note_edit' else text.replace('skill.Python.years: 2','skill.Python.years: 1'))
                p.evidence_refresh(str(file.parent),['facts.md'],str(root/'cache.sqlite'))
            counts={};start=time.perf_counter()
            kwargs=dict(workers=4)
            if hardened:kwargs.update(reviewer=review,reviewer_id='offline-replay-review-adapter')
            result=prepare_jobs(p,ids,extract,draft,**kwargs)
            decisions=[]
            for item in result['jobs']:
                decision=item.get('screen',{}).get('decision')
                if not decision:decision='excluded' if item.get('held')=='Outside default role families' else 'needs_input'
                reference=rows[item['job_id']]['reference']['decision']
                decisions.append(dict(job_id=item['job_id'],company=rows[item['job_id']]['company'],decision=decision,reference=reference,
                                      questions=item.get('screen',{}).get('questions',[]),failures=item.get('screen',{}).get('failures',[])))
            summary={d:sum(j['decision']==d for j in decisions) for d in ('eligible','excluded','needs_input')}
            phases.append(dict(phase=phase,seconds=time.perf_counter()-start,callback_counts=counts,metrics=result['metrics'],decisions=decisions,
                summary=summary,decision_disagreements=sum(j['decision']!=j['reference'] for j in decisions),
                unsafe_passes=sum(j['decision']=='eligible' and j['reference']!='eligible' for j in decisions),
                unnecessary_holds=(sum(j['decision']=='needs_input' and j['reference']=='eligible' for j in decisions) if any(j['reference']=='eligible' for j in decisions) else None),
                eligible_reference_count=sum(j['reference']=='eligible' for j in decisions)))
        p.store.db.close()
    report=dict(label=args.label,kind='offline deterministic replay; NO model or browser calls',jobs=len(ids),
                provider_tokens=None,provider_cost=None,model_factual_error_rate=None,external_submissions=0,phases=phases)
    Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({**{k:v for k,v in report.items() if k!='phases'},'phases':[{k:v for k,v in p.items() if k not in ('metrics','decisions')} for p in phases]},indent=2))

if __name__=='__main__':main()
