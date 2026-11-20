from pprint import pprint

from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode
from region_runtime.contract import (
    RULESET,
    fingerprint,
)

CENTRAL_URL = "http://157.85.96.133:9001"

OPERATION_ID = "move-test-008"

central = HttpPeer(
    CENTRAL_URL
)

node_a = RegionNode(
    "A",
    b"A" * 32,
)

#
# register
#
central.call(
    "register",
    node_id="A",
)

#
# lease
#
grant_a = central.call(
    "grant",
    node_id="A",
)

node_a.grant(
    grant_a
)

print(
    "A EPOCH =",
    node_a.epoch,
)

#
# snapshot
#
snapshot = central.call(
    "snapshot"
)

sync_a = node_a.sync(
    snapshot
)

#
# activate
#
activate = central.call(
    "activate",
    node_id="A",
    ready=sync_a,
    ttl=30,
)

print("\n=== ACTIVATE ===")
pprint(activate)

#
# current sequence
#
current_seq = snapshot["state"]["seq"]

proposal = {
    "operation_id": OPERATION_ID,
    "epoch": node_a.epoch,
    "ruleset": RULESET,
    "base_version": snapshot["version"],
    "base_hash": snapshot["state_hash"],
    "base": snapshot["state"],
    "commands": [
        {
            "seq": current_seq + 1,
            "actor": "p1",
            "dx": 1,
            "dy": 0,
        }
    ],
}

proposal["input_hash"] = fingerprint(
    proposal["commands"]
)

#
# prepare only
#
prepare = central.call(
    "prepare",
    proposal=proposal,
)

print("\n=== PREPARE ===")
pprint(prepare)

print(
    "\n=== STOP HERE ==="
)

print(
    "Operation is intentionally left in PENDING state."
)

status = central.call(
    "status",
    operation_id=OPERATION_ID,
)

print("\n=== STATUS ===")
pprint(status)