"""Strict local laboratory contract. No production transport or credentials."""
import hashlib
import hmac
import json
import re

RULESET = "region-fixture-movement-v1"
MAX_BATCH = 128

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()

def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Invalid identifier")
    return value

def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Invalid bounded integer")
    return value

def state_valid(state):
    if not isinstance(state, dict) or set(state) != {"seq", "positions", "walls"}:
        raise ValueError("Invalid state schema")
    integer(state["seq"], 0, 10**12)
    if not isinstance(state["positions"], dict) or not 1 <= len(state["positions"]) <= 32:
        raise ValueError("Invalid positions")
    for actor, point in state["positions"].items():
        identifier(actor)
        point_valid(point)
    if not isinstance(state["walls"], list) or len(state["walls"]) > 256:
        raise ValueError("Invalid walls")
    for point in state["walls"]:
        point_valid(point)
    if len({tuple(p) for p in state["walls"]}) != len(state["walls"]):
        raise ValueError("Duplicate walls")
    if any(point in state["walls"] for point in state["positions"].values()):
        raise ValueError("Actor inside wall")
    return state

def point_valid(point):
    if not isinstance(point, list) or len(point) != 2:
        raise ValueError("Invalid point")
    for v in point:
        integer(v, -10000, 10000)

def commands_valid(commands, state):
    if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_BATCH:
        raise ValueError("Invalid command batch size")
    seq = state["seq"]
    for command in commands:
        if not isinstance(command, dict) or set(command) != {"seq", "actor", "dx", "dy"}:
            raise ValueError("Invalid command schema")
        seq += 1
        if integer(command["seq"], 1, 10**12) != seq:
            raise ValueError("Command sequence gap or duplicate")
        if command["actor"] not in state["positions"]:
            raise ValueError("Unknown actor")
        integer(command["dx"], -1, 1)
        integer(command["dy"], -1, 1)
        if abs(command["dx"]) + abs(command["dy"]) > 1:
            raise ValueError("Only cardinal fixture steps or wait allowed")
    return commands

def signed(key, body):
    return {"body": body, "proof": hmac.new(key, canonical(body), hashlib.sha256).hexdigest()}

def checked(key, envelope):
    if not isinstance(envelope, dict) or set(envelope) != {"body", "proof"}:
        raise ValueError("Invalid signed envelope")
    proof = envelope["proof"]
    if not isinstance(proof, str) or not hmac.compare_digest(signed(key, envelope["body"])["proof"], proof):
        raise ValueError("Invalid role proof")
    return envelope["body"]

def fixture():
    return {"seq": 0, "positions": {"p1": [0, 0], "p2": [0, 3]}, "walls": [[2, 0]]}
