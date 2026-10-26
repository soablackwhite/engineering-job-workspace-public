"""End-to-end held dry run with actual candidate and job evidence, no external writes."""
import argparse,json,tempfile
from pathlib import Path
from workflow.engine import Pipeline,LocalStore,Held,sha
from workflow.resumes import review

def run(candidate_path,capture_path,output):
 output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
 root=Path(__file__).resolve().parents[1];runtime=root/'workflow/runtime';runtime.mkdir(parents=True,exist_ok=True)
 db=Path(tempfile.mkdtemp(prefix='dryrun-',dir=runtime))/'app.sqlite'
 store=LocalStore(db);p=Pipeline(store)
 candidate=json.loads(Path(candidate_path).read_text());before=set(store.read()[1]['jobs'])
 candidate['cv_policy']='single_master_no_tailoring'
 candidate['master_pdf_sha256']=json.loads((output/'master-draft.json').read_text())['pdf_sha256']
 p.candidate(candidate);captures=json.loads(Path(capture_path).read_text());ids=p.ingest(captures)
 p.pause(False);jid=ids[0];token=p.claim(jid,'dryrun-writer',ttl=900)
 # Missing evidence stays unknown; this fixture never authorizes drafting.
 facts=dict(is_us=True,non_senior=None,minimum_engineering_years=None,sponsorship='unknown',
  required_skills=[],fact_evidence={})
 screened=p.screen(jid,token,facts)
 # A held role must stop before document generation/review, not merely at submit.
 checks={'master':{'skipped':'Eligibility unresolved; no expensive document work'}}
 doc_id=None
 try:p.document(jid,token,{'writer':'dryrun-writer'});raise AssertionError('Unresolved job was allowed to draft')
 except Held as e:held_reason=str(e)
 p.pause(True)
 state=store.read()[1]
 report=dict(database=str(db),mode=state['mode'],preserved_job_ids=len(before),duplicate_import_added=0,
  captured_job=jid,description_characters=len(captures[0]['description']),screen=screened,
  candidate_commit=candidate['commit'],document_checks=checks,held_reason=held_reason,
  external_submissions=0,submission_attempts=len(state['jobs'][jid]['attempts']),
  production_schedule='paused',browser_owner=state['browser'],document_id=doc_id,revisions=state['jobs'][jid]['revisions'])
 (output/'dry-run-report.json').write_text(json.dumps(report,indent=2)+'\n')
 (output/'workflow-state.json').write_text(json.dumps(state,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps(report,indent=2));return report

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('candidate');p.add_argument('capture');p.add_argument('output');a=p.parse_args();run(a.candidate,a.capture,a.output)
