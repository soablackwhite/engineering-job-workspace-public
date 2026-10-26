"""Read private operator configuration without embedding identity or credentials."""
from pathlib import Path
import json
import os
from urllib.parse import urlsplit

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / '.private' / 'workspace.json'
ENV = {'service_origin':'JOB_CRM_ORIGIN', 'receipt_account':'JOB_RECEIPT_ACCOUNT',
       'supplemental_account':'JOB_SUPPLEMENTAL_ACCOUNT', 'keychain_service':'JOB_KEYCHAIN_SERVICE',
       'keychain_account':'JOB_KEYCHAIN_ACCOUNT', 'hermes_root':'JOB_HERMES_ROOT',
       'hermes_python':'JOB_HERMES_PYTHON', 'resume_manifest':'JOB_RESUME_MANIFEST',
       'selection_policy':'JOB_SELECTION_POLICY', 'candidate_index':'JOB_CANDIDATE_INDEX'}

def config():
    path = Path(os.environ.get('JOB_WORKSPACE_CONFIG') or DEFAULT_CONFIG).expanduser()
    if not path.exists(): return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) - ENV.keys():
        raise ValueError('Private workspace configuration has unsupported fields')
    if any(not isinstance(v, str) for v in value.values()):
        raise ValueError('Private workspace configuration values must be strings')
    # Optional blank fields in the public example are unconfigured, not errors.
    return {k:v for k,v in value.items() if v.strip()}

def value(key, *, required=False):
    override = os.environ.get(ENV[key])
    result = override if isinstance(override,str) and override.strip() else config().get(key)
    if required and (not isinstance(result, str) or not result.strip()):
        raise ValueError('Configure private workspace field: ' + key)
    return result

def service_origin():
    origin = value('service_origin', required=True)
    parts = urlsplit(origin)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
        or parts.path not in ('', '/') or parts.query or parts.fragment):
        raise ValueError('Configured CRM destination must be a bare HTTPS origin')
    return origin.rstrip('/')


def require_operational_context():
    """Hold all real service/provider/browser entry before private bindings exist."""
    from workflow.target_policy import installed,policy_digest
    from workflow.resumes import verified_manifest
    from workflow.evidence import current,fact_value
    service_origin()
    policy=json.loads(Path(value('selection_policy',required=True)).expanduser().read_text())
    if not installed(policy) or policy.get('digest')!=policy_digest(policy):
        raise ValueError('Reviewed enabled private selection policy required')
    manifest=verified_manifest()
    if policy.get('current_cv_sha256')!=manifest['pdf_sha256']:
        raise ValueError('Private policy and approved PDF do not agree')
    index=json.loads(Path(value('candidate_index',required=True)).expanduser().read_text())
    if not isinstance(index,dict) or not index.get('records') or not current(index):
        raise ValueError('Current reviewed private candidate index required')
    if fact_value(index,'document.master.sha256')!=manifest['pdf_sha256']:
        raise ValueError('Private candidate index and approved PDF do not agree')
