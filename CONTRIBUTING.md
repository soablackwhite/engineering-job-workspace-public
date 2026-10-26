# Contributing

Use issues and pull requests for source improvements, with a concrete problem statement and relevant test results. Never attach real candidate documents, career notes, mailbox messages, browser sessions or production exports. Use reserved example domains and synthetic data.

Install the locked dependencies and run README.md checks. Add meaningful tests when changing execution or privacy boundaries. The privacy guard runs before build in CI; optional local hooks are under `.githooks/`. Review staged changes even when the guard passes.

Keep shared qualification/authentication/revision/review gates across every entry point. Unknown evidence holds work; tests must remain independent of private accounts and services. Do not introduce automatic deployment or job submission in CI.

Source history must remain clean. Do not merge private operational history or upload a bundle containing it. Keep a separate ignored operator configuration and private backup.
