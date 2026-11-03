# Gameplay Worker M2 handoff

Additive to v035+Foundation+M1. Runtime files unchanged. 34new tests; total238.
nearby_snapshot_v1 read-only gameplay task, not synthetic hash challenge.
Trusted bridge SELECTs actual World geometry (max64entities); worker gets no
credentials/private fields but sees all bounded geometry incl far entities.
Central separate reference verifier checks complete structured result; stores
receipt digest; consume reconstructs and checks current geometry hash+TTL5s.
No runtime look/nearby replacement, no mutation, no rewards, no performance claim.
Loopback only, opt-in Demo uses real World fixtures and real worker subprocess.
Use separate coordinator DB, never mix M1 jobs. Snapshot expiry terminal on claim.
Read docs/GAMEPLAY_WORKER_M2_TH.md and protocol/gameplay-worker-m2.json.
Windows validation pending. No need to stop/update production Nodes.
