# R1-B Frontier Separation v1

## Meaning
- replication_frontier is the historical maximum validated matched tick from
  any one replica during the current primary process lifetime. It bounds the
  primary lead only. It is not a quorum, current availability, verifier approval,
  durable commit frontier, or a failover selection criterion.
- stored_tick is a replica's durable receive progress.
- applied_tick is local deterministic replay progress, not gameplay approval.
- reported_matched_tick is the replica report; the primary still validates it
  against source hashes through the existing validate_ack path.
- safe_frontier remains a deprecated, read-only compatibility alias for
  replication_frontier. New consumers must use the explicit frontier fields.

## Verification is intentionally not implemented
frontiers.verification.status = NOT_IMPLEMENTED
verified_tick = 0
committed_tick = 0
verified_checkpoint = null
failover_eligible = false

Zero is a compatibility sentinel, NOT an approved genesis checkpoint.
Replication ACKs may not assert nonzero verification/commitment counters.
There is no checkpoint submission command, receipt verifier, persistence schema,
checkpoint issuer, promotion endpoint, or new trusted key in this patch.
Do not promote a replica using any replication frontier or matching state hash.

## Interfaces
Primary snapshot and replica control-plane snapshot add a nested frontiers view.
Replica status() remains the exact existing wire ACK schema; local_snapshot()
adds presentation fields only. Network message format and transport are unchanged.
Existing hold_replay/hold_network responses retain their existing status shape.
The snapshot is an observation, not an atomic distributed checkpoint.

## Preserved behavior
Replay fencing remains byte-for-byte unchanged in existing methods.
The lead bound, historical max-any-one policy, epoch policy and backpressure
behavior are unchanged. No DB migrations, production enablement or failover.

## Future checkpoint pipeline (not implemented here)
Requires a separately reviewed contract: trusted verifier identity and authority,
region/epoch/ruleset and source evidence binding, checkpoint ID and state/log
hashes, verified-through frontier, pending operations and reserved materials,
last accounted game time, idempotent durable Central acceptance, revocation and
freshness policy, and promotion fencing. Never infer this proof from replication.

## Installation and tests
Apply only to a stopped isolated test copy after the replay-fencing patch.
Installer pins complete source-file byte SHA-256 values, backs up modified files,
adds 24 tests, and runs the full exact-count gate once: 612 tests, skipped=0.
Focused gate count is 102; do not rerun it after a successful full gate.
No runtime tests were executed while preparing this patch.
The installer does not open DB/profile/key files. Existing runtime tests execute
on the target; use only an isolated test copy and inspect their isolation policy.
On gate failure the installer attempts source rollback and keeps the logs.
After power loss, stop all processes and use the backup manifest for rollback.
