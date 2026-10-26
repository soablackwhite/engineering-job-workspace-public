# Workflow modules

This directory contains the reusable workflow engine and optional transports. It contains no operator profile, approved CV, mailbox identity or execution grant. Read the root README, architecture and private setup before configuring real operations.

`python3 -m workflow.cli --help` describes available commands. Local state belongs in ignored runtime/private storage. Remote operations require an explicitly configured origin and private authentication. Mailbox operations require an explicit verified account. Approved document bytes and manifest are supplied privately, not generated from public candidate defaults.

Capture full exact-role evidence, preserve current source hashes and separate mandatory requirements from preferred ones. Review against confirmed, scoped facts. Missing facts or evidence hold the affected job; do not guess or silently substitute overall tenure for a tool-specific duration.

All browser paths share the same state/qualification/document/duplicate/review/authorization gates. One owner controls the browser. Optional historical adapter implementations are not permission to start a service or run a recorded sequence. Keep execution paused until deliberately authorized; never bypass a platform restriction.

See UPGRADE.md for evidence contracts, PILOT.md for the transport boundary, EMAIL_RECONCILIATION.md for optional mailbox processing and STORAGE.md for persistence.
