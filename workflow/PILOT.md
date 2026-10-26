# Supervised browser transport

Browser implementations are optional adapters. They are disabled by default and do not establish operator permission, sign-in, document selection or employer consent. Read docs/PRIVATE_SETUP.md and configure only an explicitly supported runtime.

Use one browser owner and preserve leases, exact job/attempt identity, current review bindings and authorization gates. Decide each action from current visible state. Do not replay recorded browser steps or bypass platform restrictions, login walls or CAPTCHAs. Stop on an unknown required control, preserve the draft and hold only the affected job.

Use the offline synthetic fixture tests for transport development. Do not point them at a real browser or production service. A local fixture pass does not prove a real upload or application was submitted.

For a real authorized operation, verify exact account identity and approved file bytes, check duplicates, inspect the final review and record the actual confirmation. An uncertain submit is reconciled before any retry. The repository carries no standing execution authorization.
