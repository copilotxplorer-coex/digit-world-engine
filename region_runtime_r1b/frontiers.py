"""Replication progress is not verifier approval or failover eligibility.

This lab has no trusted-verifier receipt ingestion, verified-checkpoint store,
or promotion API. Zero legacy verification counters mean NOT IMPLEMENTED,
not a verified genesis checkpoint. Wire ACK schema stays unchanged.
"""

SCHEMA = "r1b-frontiers-v1"


def _tick(value):
    if type(value) is not int or value < 0 or value > 10000:
        raise ValueError("Tick must be an integer in [0,10000]")
    return value


def require_unverified(verified_tick, committed_tick):
    if type(verified_tick) is not int or type(committed_tick) is not int:
        raise ValueError("Legacy verification counters must be integer zero")
    if verified_tick != 0 or committed_tick != 0:
        raise ValueError("Replication cannot assert verification or commitment")


def _verification():
    return {"status": "NOT_IMPLEMENTED", "verified_tick": 0,
            "committed_tick": 0, "verified_checkpoint": None,
            "failover_eligible": False,
            "reason": "NO_TRUSTED_VERIFIER_CHECKPOINT_PIPELINE"}


def primary_frontiers(local_tick, replication_tick):
    local_tick = _tick(local_tick); replication_tick = _tick(replication_tick)
    if replication_tick > local_tick:
        raise ValueError("Replication frontier exceeds local log")
    return {"schema": SCHEMA,
            "replication": {"local_log_tick": local_tick,
                "replication_frontier_tick": replication_tick,
                "policy": "HISTORICAL_MAX_MATCHED_ANY_ONE_REPLICA",
                "scope": "CURRENT_PRIMARY_PROCESS_LIFETIME",
                "purpose": "BOUND_PRIMARY_LEAD_ONLY",
                "current_availability_guaranteed": False,
                "quorum_guaranteed": False,
                "verified_checkpoint": False},
            "verification": _verification()}


def replica_frontiers(status):
    require_unverified(status["verified_tick"], status["committed_tick"])
    stored = _tick(status["stored_tick"]); applied = _tick(status["applied_tick"])
    matched = _tick(status["matched_tick"])
    if applied > stored or matched != applied:
        raise ValueError("Invalid replica progress ordering")
    return {"schema": SCHEMA,
            "replication": {"stored_tick": stored, "applied_tick": applied,
                "reported_matched_tick": matched,
                "purpose": "REPLICATION_AND_DETERMINISTIC_REPLAY_ONLY",
                "primary_ack_validation_required": True,
                "verified_checkpoint": False},
            "verification": _verification()}
