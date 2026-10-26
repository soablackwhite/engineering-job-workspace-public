# Privacy and public releases

The repository is reusable source, not a personal database. Keep real candidate facts, CVs/source/manifest hashes, mailbox settings/content, application records, employer correspondence, browser artifacts, credentials and deployment bindings in private storage. `.private/`, runtime/output folders and common document/export formats are ignored.

`.gitignore` does not remove tracked files or old commits. Review staged contents and run `python3 scripts/check_public_tree.py`. The optional pre-commit hook and CI run the same generic checks. They detect risky paths, credential signatures, non-example mailbox literals and machine home paths. Automated checks complement source review; they cannot recognize every personal narrative or prove that an arbitrary string is harmless.

## Publishing code that previously contained personal data

Prepare a sanitized source export with no inherited `.git` directory. Replace private seeds with empty/synthetic examples; move all operator configuration outside source; remove real documents and historical runbooks. Independently review the complete export and run tests before creating a fresh root commit.

Publish into a **fresh repository** with no private branches, pull requests, tags, release assets, Actions artifacts or imported history. Keep the old operational repository private. Do not merely change its visibility or merge its branches into the public tree: deleting files in a new commit leaves old versions available. Rewriting history also needs coordinated cleanup of PR refs and caches; see [GitHub's guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

Verify the public repository's owner, complete refs/history and tracked contents before changing visibility. Re-run `python3 scripts/check_public_tree.py --history` after initializing the sanitized Git repository. Keep exact personal regression markers and audit exports outside the public tree; a scanner denylist containing someone's real email or CV hash would itself expose it.

If a real credential was committed, rotate/revoke it and assess all copies. Do not publish a secret value in a report or issue. See [security reporting](../SECURITY.md).
