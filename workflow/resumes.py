"""Reuse an operator-approved private fixed artifact; never generate or tailor a CV."""
import argparse,hashlib,json,re,shutil,unicodedata
from pathlib import Path
from workflow.runtime_config import value

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def manifest_path():
    return Path(value('resume_manifest',required=True)).expanduser().resolve()

def artifact_paths(manifest=None):
    path=manifest_path();m=manifest if manifest is not None else json.loads(path.read_text())
    result=[]
    for key in ('source_path','pdf_path'):
        raw=m.get(key)
        if not isinstance(raw,str) or not raw:raise ValueError('Private manifest missing artifact path: '+key)
        p=Path(raw).expanduser();result.append(p.resolve() if p.is_absolute() else (path.parent/p).resolve())
    return tuple(result)

def verified_manifest():
    manifest=json.loads(manifest_path().read_text())
    if not isinstance(manifest,dict):raise ValueError('Private master manifest must be an object')
    for key in ('source_sha256','pdf_sha256'):
        v=manifest.get(key)
        if not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{64}',v):raise ValueError('Master manifest missing or invalid hash: '+key)
    source,pdf=artifact_paths(manifest)
    if digest(source)!=manifest['source_sha256'] or digest(pdf)!=manifest['pdf_sha256']:
        raise ValueError('Master artifact changed; review and explicitly revise the private manifest')
    if not pdf.read_bytes().startswith(b'%PDF-'):raise ValueError('Approved artifact must be a PDF')
    if type(manifest.get('required_pages')) is not int or manifest['required_pages']<1:raise ValueError('Private manifest needs a positive required_pages')
    for key in ('sections','required_entries'):
        if not isinstance(manifest.get(key),list) or any(not isinstance(x,str) or not x for x in manifest[key]):raise ValueError('Private manifest needs explicit coverage: '+key)
    if not isinstance(manifest.get('candidate_commit'),str) or not manifest['candidate_commit']:raise ValueError('Private manifest needs candidate source binding')
    return manifest

def write(candidate,variant='master'):
    if variant!='master':raise ValueError('Dynamic CV variants are disabled; use the single master CV')
    m=verified_manifest()
    if candidate.get('commit')!=m['candidate_commit']:raise ValueError('Candidate sources changed; review the master before reusing it')
    evidence={e['id'] for e in candidate.get('evidence',[])}
    for row in m.get('employment_provenance',[]):
        if any(eid not in evidence for eid in row.get('evidence_ids',[])):raise ValueError('Master employment evidence is missing')
    return dict(variant='master',writer='static-editorial-master',candidate_commit=candidate['commit'],
        name=m.get('name'),title=m.get('title'),pdf_sha256=m['pdf_sha256'],source_sha256=m['source_sha256'],
        held=True,hold_reasons=candidate.get('needs_input',[]),requires_employer_visa_sponsorship=candidate.get('requires_employer_visa_sponsorship'),
        manifest=m,language=m.get('language','English'),dynamic_tailoring=False)

def render(doc,path):
    m=verified_manifest()
    if doc.get('variant')!='master' or doc.get('pdf_sha256')!=m['pdf_sha256']:raise ValueError('Only the canonical master artifact may be used')
    source,pdf=artifact_paths(m);path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.resolve()!=pdf:shutil.copyfile(pdf,path)
    output_source=path.with_suffix(source.suffix or '.source')
    if output_source.resolve()!=source:shutil.copyfile(source,output_source)
    return m['pdf_sha256']

def review(doc,candidate,pdf):
    from pypdf import PdfReader
    m=verified_manifest();issues=[]
    for key,v in write(candidate).items():
        if doc.get(key)!=v:issues.append('master_metadata_changed:'+key)
    if digest(pdf)!=m['pdf_sha256']:issues.append('master_pdf_changed')
    reader=PdfReader(pdf)
    if len(reader.pages)!=m['required_pages']:issues.append('master_page_count_changed')
    text=' '.join(p.extract_text() or '' for p in reader.pages)
    norm=lambda v:re.sub(r'\s+','',unicodedata.normalize('NFKC',v))
    for entry in m['sections']+m['required_entries']:
        if norm(entry) not in norm(text):issues.append('coverage_missing:'+entry)
    return dict(passed=not issues,issues=issues,page_count=len(reader.pages),pdf_sha256=digest(pdf),
        coverage={'sections':m['sections'],**m.get('coverage',{})},semantic_passed=False,
        checks=['canonical PDF/source hashes','section and entry coverage','candidate evidence binding'],
        limitations=['Mechanical checks do not approve factual claims, eligibility, browser access or submission.'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['write','review']);p.add_argument('candidate');p.add_argument('variant',choices=['master']);p.add_argument('directory');a=p.parse_args()
    candidate=json.loads(Path(a.candidate).read_text());out=Path(a.directory);out.mkdir(parents=True,exist_ok=True)
    pdf=out/'approved-cv.pdf';jp=out/'master-draft.json'
    if a.action=='write':
        doc=write(candidate);render(doc,pdf);jp.write_text(json.dumps(doc,indent=2)+'\n');jp.chmod(0o600)
        print(json.dumps({'pdf':str(pdf)}))
    else:
        result=review(json.loads(jp.read_text()),candidate,pdf);target=out/'master-review.json';target.write_text(json.dumps(result,indent=2)+'\n');target.chmod(0o600);print(json.dumps(result))
