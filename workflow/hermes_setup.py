"""Create dedicated profiles and a project board through installed Hermes CLI."""
import json,os,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'workflow/runtime'
ROLES={
 'coordinator':'Coordinate six-hour incremental ingestion and native Kanban dependencies. Never submit applications.',
 'capture':'Capture complete job descriptions and evidence, preserve source dates, deduplicate through the CRM bridge.',
 'writer':'Write source-grounded platform/AI resumes only from candidate evidence IDs. Never fabricate experience.',
 'reviewer':'Independently review factual support, hard constraints, skill coverage and document quality. Request bounded revisions.',
 'browser':'Own supervised application browser operations. Current implementation is dry-run only. Never click final submit.'}

def env():return dict(os.environ,HERMES_KANBAN_HOME=str(RUNTIME/'kanban'),HERMES_KANBAN_BOARD='job-pipeline')
def run(*args):
    r=subprocess.run([shutil.which('hermes') or str(Path.home()/'.local/bin/hermes'),*args],env=env(),cwd=ROOT,text=True,capture_output=True)
    if r.returncode:raise RuntimeError(r.stderr or r.stdout)
    return r.stdout

def setup(*, authorized=False):
    if authorized is not True:raise RuntimeError("Explicit profile-setup authorization required; no scheduler or browser is started by default")
    from workflow.runtime_config import require_operational_context
    require_operational_context()
    import yaml
    RUNTIME.mkdir(parents=True,exist_ok=True)
    # Dedicated profiles use Hermes' native read-only fallback to existing provider authentication.
    global_config=yaml.safe_load((Path.home()/'.hermes/config.yaml').read_text()) or {}
    for role,description in ROLES.items():
        name='jobs-'+role;home=Path.home()/'.hermes/profiles'/name
        if not home.exists():
            run('profile','create',name,'--no-alias','--no-skills','--description',description)
            config={'model':global_config.get('model',{}),'terminal':{'backend':'local','cwd':str(ROOT)},
                    'agent':{'max_turns':30},'kanban':{'review_dispatch':True},
                    'browser':{'use_real_profile':False,'inactivity_timeout':120}}
            (home/'config.yaml').write_text(yaml.safe_dump(config))
            (home/'SOUL.md').write_text((ROOT/'workflow/profiles'/f'{role}.md').read_text())
            skills=home/'skills/job-pipeline';skills.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/'workflow/profiles/SKILL.md',skills/'SKILL.md')
            if role in ('coordinator','writer','browser'):
                canonical=Path.home()/'.codex/skills/job-application-browser'
                if (canonical/'SKILL.md').is_file():
                    (home/'skills/job-application-browser').symlink_to(canonical,target_is_directory=True)
    plugin_home=Path.home()/'.hermes/profiles/jobs-browser/plugins/job-application'
    if not plugin_home.exists():
        plugin_home.mkdir(parents=True)
        for name in ('__init__.py','plugin.yaml'):shutil.copyfile(ROOT/'workflow/hermes_plugin'/name,plugin_home/name)
        (plugin_home/'project-root.json').write_text(json.dumps({'root':str(ROOT)})+'\n')
        run('-p','jobs-browser','plugins','enable','job-application')
    marker=RUNTIME/'setup.json'
    if marker.exists():return json.loads(marker.read_text())
    run('kanban','boards','create','job-pipeline','--name','Job pipeline','--default-workdir',str(ROOT))
    tasks=[];parent=None
    for role in ('capture','writer','reviewer','browser'):
        args=['kanban','--board','job-pipeline','create',f'Dry-run {role}',
          '--body',f'Read the job-pipeline skill and workflow/profiles/{role}.md; consult only needed README sections. All external submissions are disabled. Use the app database; report exact held outcomes. This initial card is blocked pending supervised dispatch.',
          '--assignee','jobs-'+role,'--workspace','dir:'+str(ROOT),'--max-runtime','10m',
          '--max-retries','1','--completion-contract','local-only','--idempotency-key','initial-'+role,
          '--initial-status','blocked','--json']
        if parent:args.extend(['--parent',parent])
        result=json.loads(run(*args));task=result.get('task',result);parent=task['id'];tasks.append(dict(role=role,id=parent))
    cron=run('-p','jobs-coordinator','cron','create','every 6h',
      'Read the job-pipeline skill and relevant README sections as needed. Run incremental discovery, then create dependent native Kanban capture, writer and reviewer tasks. Browser remains dry-run only. Respect app pause; no external submissions.',
      '--name','Job pipeline - six-hour intake','--workdir',str(ROOT),'--paused',
      '--paused-reason','Awaiting reviewed dry run and supervised pilot','--deliver','local','--continuity')
    result=dict(tasks=tasks,cron=cron,board_home=str(RUNTIME/'kanban'),profiles=['jobs-'+r for r in ROLES])
    marker.write_text(json.dumps(result,indent=2)+'\n');return result
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--configure-profiles',action='store_true');a=p.parse_args()
    print(json.dumps(setup(authorized=a.configure_profiles),indent=2))
