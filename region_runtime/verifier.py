"""Trusted reference replay; intentionally does not import Node simulation."""
import copy
from .contract import RULESET, state_valid, commands_valid, fingerprint, signed

def reference(base, commands):
    state_valid(base)
    commands_valid(commands, base)
    positions = copy.deepcopy(base["positions"])
    seq = base["seq"]
    for event in commands:
        x, y = positions[event["actor"]]
        x += event["dx"]
        y += event["dy"]
        if not (-10000 <= x <= 10000 and -10000 <= y <= 10000):
            raise ValueError("Reference boundary exceeded")
        if [x, y] not in base["walls"]:
            positions[event["actor"]] = [x, y]
        seq = event["seq"]
    return state_valid({"seq": seq, "positions": positions, "walls": copy.deepcopy(base["walls"])})

def verify(proposal, key):
    if proposal["ruleset"] != RULESET:
        raise ValueError("Unknown ruleset")
    if fingerprint(proposal["base"]) != proposal["base_hash"]:
        raise ValueError("Base hash mismatch")
    if fingerprint(proposal["commands"]) != proposal["input_hash"]:
        raise ValueError("Input hash mismatch")
    state = reference(proposal["base"], proposal["commands"])
    body = {k: proposal[k] for k in ("operation_id", "epoch", "base_version", "base_hash", "input_hash", "ruleset")}
    body.update(state=state, state_hash=fingerprint(state))
    return signed(key, body)
