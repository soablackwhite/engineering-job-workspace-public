"""Synthetic, offline benchmark; no real model token or LinkedIn speed claims."""
import json
from pathlib import Path
import socket
import tempfile
import time
from unittest.mock import patch
from workflow.engine import LocalStore, Pipeline
from workflow.runner import prepare_jobs, release_claim
from workflow.tests.fixtures import candidate, extraction


def sample(workers, count=12):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);p=Pipeline(LocalStore(root/'state.sqlite'))
        p.candidate(candidate(root,years=2));p.pause(False)
        ids=p.ingest([dict(id=str(i),title='Applied AI Engineer',company='Synthetic',url=f'https://example.test/{i}',
                          description=f'Synthetic complete job, {2 if i%2==0 else 5} years.',checked_at='2026-10-07') for i in range(count)])
        def extract(job,context):
            time.sleep(.02)  # Simulated model latency, explicitly not real throughput.
            return extraction(job,{'minimum_years':2 if int(job['id'])%2==0 else 5})
        def draft(job,context):
            time.sleep(.02)
            return dict(writer='synthetic',variant='master',pdf_sha256='fixture-pdf')
        cold=prepare_jobs(p,ids,extract,draft,workers=workers,reviewer=lambda j,c:extraction(j,{'minimum_years':2 if int(j['id'])%2==0 else 5}),reviewer_id='synthetic-reviewer')
        for item in cold['jobs']:
            if item.get('claim_token'):release_claim(p,item['job_id'],item['claim_token'])
        warm=prepare_jobs(p,ids,extract,draft,workers=workers,reviewer=lambda j,c:extraction(j,{'minimum_years':2 if int(j['id'])%2==0 else 5}),reviewer_id='synthetic-reviewer')
        p.store.db.close()
        return dict(workers=workers,jobs=count,cold=cold['metrics'],warm=warm['metrics'])


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Network disabled')):
        report=dict(synthetic_only=True,simulated_callback_latency_seconds=.02,
                    real_model_tokens='unmeasured',external_submissions=0,samples=[sample(1),sample(4)])
    output=json.dumps(report,indent=2)+'\n'
    if args.output:Path(args.output).write_text(output)
    print(json.dumps({**{k:v for k,v in report.items() if k!='samples'},'samples':[
        dict(workers=s['workers'],cold_seconds=s['cold']['seconds'],warm_seconds=s['warm']['seconds'],
             cold_extract_calls=s['cold']['extract_calls'],cold_draft_calls=s['cold']['draft_calls'],
             warm_extract_calls=s['warm']['extract_calls'],warm_draft_calls=s['warm']['draft_calls'],
             reused_documents=s['warm']['document_reuse']) for s in report['samples']]}))

if __name__=='__main__':main()
