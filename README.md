# Engineering job workspace

An evidence-based job-search CRM and supervised application workflow. It separates job research, candidate facts, qualification review, application attempts and verified outcomes. It is designed for a human operator: a repository clone never authorizes browser actions or submissions.

The public repository contains reusable code, schemas, documentation and synthetic tests. **It contains no candidate CV, personal answers, mailbox settings, application history or production service configuration.** Each operator supplies that information privately. The application starts empty and execution remains paused until explicitly configured and authorized.

## How it works

1. Capture an exact job description and its source evidence.
2. Compare every mandatory requirement with reviewed, scoped candidate facts.
3. Check role-specific sponsorship, location, compensation and duplicate evidence.
4. Bind the review to the exact job, fact snapshot, approved CV and policy hashes.
5. Prepare answers using only confirmed facts; hold individual unknowns for review.
6. Use one authorized browser owner, verify an actual confirmation, then save/read back the CRM record.

The included selection-policy example illustrates FDE/Solutions Engineer targeting. It is disabled and is not an operator's personal policy. Configure your own reviewed policy, approved CV and candidate snapshot before using the workflow.

See [architecture](ARCHITECTURE.md), [private setup](docs/PRIVATE_SETUP.md), [operations](docs/OPERATIONS.md) and [privacy](docs/PRIVACY.md).

## Development

Use Node.js 24 and Python 3.12 or later:

```sh
npm ci
python3 -m venv .venv
.venv/bin/python -m pip install -r workflow/requirements.txt
.venv/bin/python scripts/check_public_tree.py
.venv/bin/python -m unittest discover -s workflow/tests -p 'test_*.py'
node --experimental-strip-types --import ./workflow/test-network-off.mjs --test tests/*.test.mjs workflow/tests/*.test.mjs
node --import tsx --import ./workflow/test-network-off.mjs --test workflow/tests/*.test.ts
npm run check:types
npm run build
```

`npm run dev` provides an empty local UI preview. Hosted APIs require a configured authentication boundary and storage bindings; an unconfigured clone must not expose private state. This is a source toolkit, not a hosted multi-user service or a promise of portable provider authentication. See private setup for the deployment boundary.

Tests generate disposable synthetic documents and state. CI has read-only repository permissions and runs privacy checks, tests and a build only. It does not deploy, upload private artifacts, connect to a mailbox or operate application forms.

## Contributing

Use synthetic fixtures and reserved example domains. Keep personal state in ignored local storage or a separate access-controlled service. Before committing, run the privacy guard and review `git diff --cached`. Optional local hook:

```sh
git config core.hooksPath .githooks
```

See [contributing](CONTRIBUTING.md) and [security](SECURITY.md). Code is available under the [MIT license](LICENSE); third-party dependencies retain their licenses.
