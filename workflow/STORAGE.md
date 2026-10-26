# Private workflow storage

The hosted workflow now uses `r2-versioned-delta-v1`. D1 keeps the revision and a small content-addressed pointer; the existing private DOCUMENTS bucket retains complete state versions. A legacy inline D1 row remains readable and is archived before its first pointer update. No backfill or schema migration is needed.

Every write still passes `validateState`, including immutable attempts, artifacts, snapshots, source aliases, qualification and submission gates. The D1 compare-and-swap remains the commit point. Failed uploads leave the old state active; a lost race leaves an unreferenced archive but cannot replace the winner. Missing or altered archives fail closed rather than producing an empty queue.

`GET /api/workflow` returns the same complete state plus `storage_protocol`. Updated RemoteStore clients send bounded object-key deltas to `PUT`; older clients can still send complete states below the unchanged 1.5 MB request bound. Arrays are replaced atomically; null is a value. Deltas are applied to the expected revision before full-state validation. The service processes states up to 8 MiB to bound memory; reaching that later limit requires further partitioning, not deletion of history.

Private archives are not deleted by routine writes. Production verification must compare pre/post state and job-history hashes, read the public CRM projection through authenticated access, and confirm a real safe queue update exceeding the old total-state limit. Rolling back to a build that only understands inline rows is unsafe after the first pointer commit; forward repair or a verified inline export is required.
