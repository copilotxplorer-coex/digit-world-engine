# Coordinator/Worker M1 handoff

Additive to v0.3.5 + Architecture Foundation. Runtime unchanged.
Read docs/COORDINATOR_WORKER_M1_TH.md and protocol/coordinator-worker-m1.json.
Separate coordinator DB, hashed provisioned credentials, bounded validation hash
jobs, single active lease/node, heartbeat, expiry/reassignment fencing, attempts,
verified result idempotency, revoke. Loopback HTTP only. No public admin routes.
No game DB/profile/key access, no real gameplay work, no Soul Digit rewards.
Demo uses two real worker processes, four synthetic jobs and temporary files.
Tests: 30 new tests; full regression expected204. Linux tested; Windows pending.
Do not claim resource savings, anti-Sybil, production security or VPS deployment.
No automatic retry. Claim response loss requires expiry/reassignment. Results
can be acknowledged idempotently with original job/node/lease/digest.
Foundation component contract remains unchanged architecture snapshot.
