# Private operator setup

A fresh clone has no candidate data or permission to apply. Keep operational data in a separate access-controlled service or ignored `.private/` directory. Never commit a filled setup file, real document or production binding.

## Recommended laptop setup

Run `npm ci` and `npm run local`, then open the printed `/setup` address. The [guided laptop setup](LOCAL_SETUP.md) creates local storage, helps you select private note snapshots and a CV, and generates the supported Codex MCP connection command. No manual JSON configuration is needed for the local CRM and selected-source connection.

The guided setup stores an unapproved PDF and unverified source evidence. It does not silently create an approved application manifest, a verified candidate index or execution grants. The Python pipeline and hosted service configuration below are advanced, separately reviewed operations.

## Advanced pipeline and hosted configuration

```sh
mkdir -p .private/resume
cp examples/workspace.example.json .private/workspace.json
```

Edit the private copy. `JOB_WORKSPACE_CONFIG` can point to another private JSON file. Supported non-secret configuration keys and environment overrides:

| JSON key | Environment override | Purpose |
| --- | --- | --- |
| `service_origin` | `JOB_CRM_ORIGIN` | Exact trusted private CRM origin. |
| `receipt_account` | `JOB_RECEIPT_ACCOUNT` | Explicit mailbox identity to verify before reading. |
| `supplemental_account` | `JOB_SUPPLEMENTAL_ACCOUNT` | Optional additional mailbox identity. |
| `keychain_service` | `JOB_KEYCHAIN_SERVICE` | Optional credential-store service label. |
| `keychain_account` | `JOB_KEYCHAIN_ACCOUNT` | Optional credential-store account label. |
| `hermes_root` | `JOB_HERMES_ROOT` | Optional locally installed agent runtime. |
| `hermes_python` | `JOB_HERMES_PYTHON` | Optional runtime Python executable. |
| `resume_manifest` | `JOB_RESUME_MANIFEST` | Private approved fixed-CV manifest. |
| `selection_policy` | `JOB_SELECTION_POLICY` | Private reviewed selection policy. |
| `candidate_index` | `JOB_CANDIDATE_INDEX` | Current reviewed private fact snapshot. |

Do not put token values in JSON. Use explicit hidden input, `JOB_CRM_TOKEN` at runtime, or an explicitly configured private credential store. Never print values or put them in CLI arguments. Missing origin, account, model runtime, CV or required policy should hold the affected operation. Real remote calls additionally require an enabled private policy, verified CV and current reviewed fact index that agree on document identity. A locally instantiated client is not proof that execution is enabled.

The example selection policy is disabled and has no real CV binding. Copy it privately, choose your criteria, bind it to your approved PDF hash and review the configuration before importing it while paused. Follow the manifest schema/sample under `workflow/templates/`; real PDF/TeX/source/manifest files remain private. A document approval is not permission to upload it to any particular employer.

Select only current factual source notes, build a private evidence snapshot and review it. Import through revision-checked workflow operations. Do not load someone else's synthetic fixture as a real candidate profile. Keep note paths and source labels private.

## App and deployment

`npm run dev` can show an empty local interface without importing production data. Private API/MCP operations require private Worker environment values `WORKSPACE_AUTH_MODE=trusted-sites` and `WORKSPACE_OWNER_ID` set to the exact authenticated owner, plus private storage bindings. Enable this mode only behind an authenticated Sites gateway that strips/replaces caller identity headers and blocks direct Worker access. These header values are not standalone authentication. Other hosting requires a reviewed real authentication adapter. Unconfigured APIs fail closed with HTTP 503; missing/wrong identity is denied.

Provider/plugin integration is optional and requires your own account and supported tools. A clone cannot restore cookies, grant upload permissions or establish mailbox sign-in. Do not copy another installation's deployment metadata. Keep per-installation deployment config out of Git.

After setup, keep execution paused until the operator has reviewed the exact source version, private facts, policy, CV, duplicate history and browser identity. Release authorization deliberately for the intended scope; never replay historical scripts as permission.
