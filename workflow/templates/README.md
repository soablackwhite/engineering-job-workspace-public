# Private approved document manifest

This directory contains an invalid example schema only. It contains no candidate CV, source document or approved artifact hashes. Generate synthetic documents only inside temporary test directories; do not add a real PDF, TeX or Word file here.

Create the approved document and its manifest under ignored private storage, such as `.private/resume/`. Set `JOB_RESUME_MANIFEST` or the private workspace `resume_manifest` field to the manifest path. The source/PDF paths in the manifest resolve relative to that private manifest, or can be explicit private absolute paths. `workflow.resumes` never searches the repository for a candidate document and never compiles or tailors one.

Copy `resume-manifest.example.json` into private storage and replace its placeholders with reviewed metadata. `source_sha256` and `pdf_sha256` must be exact lowercase SHA-256 values of the actual approved bytes. `candidate_commit` binds the reviewed private evidence revision; each employment-provenance evidence ID must exist in the supplied private candidate object. `required_pages`, `sections` and `required_entries` define the explicit mechanical coverage checks. Optional `name`, `title`, `language` and `coverage` belong in private configuration, not the public example.

A manifest match verifies bytes and declared coverage. The document stays held for independent semantic review, qualification and separately authorized form review. It grants no deployment, browser or submission permission. A selected form filename is not proof of its document bytes. Preserve historic artifact references when deliberately approving a new revision; do not rewrite past application history.

Real service or browser entry also requires the private enabled selection policy and a current reviewed candidate index. Their approved PDF identities must agree with this manifest. The committed disabled policy example has no CV hash and cannot authorize execution.
