# Operations and recovery

Source publication, service deployment and application authorization are separate operations. Default application execution is paused. Do not resume queues, submit forms, start schedulers or replay browser scripts during setup or recovery.

## Restore code

Clone the reviewed public source and run the checks in README.md. Start with an isolated empty environment. Missing private backups are a hold, not a reason to invent candidate answers or application outcomes.

Restore private configuration independently using [private setup](PRIVATE_SETUP.md). Verify actual document bytes against the private manifest and verify the private reviewed policy/fact snapshot. Examples cannot stand in for approval.

## Restore private state

Use an access-controlled backup containing the matching D1 revision/pointer/tracking state, private R2 archives and document objects, reviewed fact snapshot and operator configuration. Git is not the private backup. Do not overwrite a nonempty service with an old local export. Restore into an isolated target first and verify that each pointer resolves to the correct content hash.

Use the compare-and-swap protocol in [storage](../workflow/STORAGE.md). Preserve job identities, attempt/receipt history, prior artifact hashes, outcomes, notes and follow-up dates. A stale write rereads and recomputes; an uncertain submission is reconciled, never blindly retried. Missing archives hold recovery.

Refresh private facts only from explicitly selected current sources. Review conflicts and provenance, then separately import/read back the snapshot under the current revision. A local index is not proof of remote synchronization. Do not import archived answers as current facts.

## Verify before release

Read back source version, service revision, pause controls, private policy digest, fact-snapshot hash and approved document identity. Expired browser leases/reviews/grants remain expired after restore. Re-establish account identity and document selection in the actual authorized browser; filenames are not byte verification.

Deployment requires the operator's intended deployment scope and a reviewed auth boundary. Application resumption requires separate explicit authorization and fresh per-job gate checks. Green CI and a successful restore do not authorize either action.
