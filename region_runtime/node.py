"""Replica cache and independent simulation; local monotonic lease checks."""
import copy
import time
from .contract import RULESET, checked, fingerprint, signed, state_valid
from .simulation import simulate

class RegionNode:
    def __init__(self, node_id, key, clock=time.monotonic):
        self.node_id, self.key, self.clock = node_id, key, clock
        self.snapshot = None
        self.epoch = 0
        self.deadline = 0

    def sync(self, snapshot):
        state_valid(snapshot["state"])
        if fingerprint(snapshot["state"]) != snapshot["state_hash"]:
            raise ValueError("Snapshot hash mismatch")
        if self.snapshot and snapshot["version"] < self.snapshot["version"]:
            raise ValueError("Snapshot version regressed")
        if self.snapshot and snapshot["version"] == self.snapshot["version"] and snapshot["state_hash"] != self.snapshot["state_hash"]:
            raise ValueError("Snapshot version conflict")
        self.snapshot = copy.deepcopy(snapshot)
        return signed(self.key, {"node_id": self.node_id, "version": snapshot["version"], "state_hash": snapshot["state_hash"]})

    def grant(self, envelope):
        body = checked(self.key, envelope)
        if body["node_id"] != self.node_id or body["epoch"] <= self.epoch:
            raise ValueError("Stale or wrong grant")
        ttl = body["ttl"]
        if type(ttl) not in (int, float) or not 0 < ttl <= 3600:
            raise ValueError("Invalid local lease")
        self.epoch = body["epoch"]
        self.deadline = self.clock() + ttl
        return {"epoch": self.epoch}

    def run(self, proposal):
        if self.clock() >= self.deadline:
            raise ValueError("Node lease expired")
        if proposal["epoch"] != self.epoch or proposal["ruleset"] != RULESET:
            raise ValueError("Wrong epoch or ruleset")
        if self.snapshot is None or proposal["base_version"] != self.snapshot["version"] or proposal["base_hash"] != self.snapshot["state_hash"]:
            raise ValueError("Replica not ready")
        if fingerprint(proposal["base"]) != self.snapshot["state_hash"] or fingerprint(proposal["commands"]) != proposal["input_hash"]:
            raise ValueError("Proposal hash mismatch")
        state = simulate(self.snapshot["state"], proposal["commands"])
        body = {k: proposal[k] for k in ("operation_id", "epoch", "base_version", "base_hash", "input_hash", "ruleset")}
        body.update(node_id=self.node_id, state=state, state_hash=fingerprint(state))
        return signed(self.key, body)
