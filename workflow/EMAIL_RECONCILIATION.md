# Optional email reconciliation

Email can corroborate application receipts and outcomes. Configure and verify the intended mailbox privately before retrieval; no mailbox identity is shipped in source. Use supported account tools; never scrape tokens or repurpose a different account when access is unavailable.

`python3 -m workflow.email_reconcile --help` describes planning and reconciliation. The optional `gmail_fetch.js` helper requires a supported host tools object and an explicit plan/account. It is not a standalone Gmail OAuth client. Cache message IDs, pagination checkpoints and MIME bodies only in private ignored storage with restrictive permissions.

Separate retrieval coverage from classification/review coverage. Follow all configured query pages and read every ID before claiming complete retrieval for those bounds; heuristic queries do not establish complete mailbox coverage. Keep a bounded overlap for incremental checks and retain unresolved identity reviews.

Parse plain and HTML MIME without executing remote content. Exclude recommendation/footer links when identifying the primary employer/requisition. Employer-name similarity is insufficient; preserve ambiguous events for review. A later delivery error, assessment invitation, viewed notification or rejection has its own lifecycle semantics. Do not automatically retry forms or start assessments.

Preview proposals, review exact identities, then apply to a fresh private state through compare-and-swap and readback. One writer owns the dataset. Preserve original submission evidence, notes and unresolved holds. Email unavailability is not evidence that no duplicate exists and does not itself authorize or prohibit unrelated applications; use the operator's explicit current policy.

Never commit mailbox caches, audit exports or filled account configuration. Rejection timing may support a clearly labelled timing-based estimate, but does not establish an employer's visa or ATS reason.
