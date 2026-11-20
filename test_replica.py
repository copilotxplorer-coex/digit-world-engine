from pprint import pprint

from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode
from region_runtime.contract import (
    RULESET,
    fingerprint,
)

CENTRAL_URL = "http://157.85.96.133:9001"

#
# Node A
#
node_a = RegionNode(
    "A",
    b"A" * 32,
)

#
# Node B
#
node_b = RegionNode(
    "B",
    b"B" * 32,
)

central = HttpPeer(
    CENTRAL_URL
)

#
# Register both
#
central.call(
    "register",
    node_id="A",
)

central.call(
    "register",
    node_id="B",
)

#
# Lease A
#
grant_a = central.call(
    "grant",
    node_id="A",
)

node_a.grant(
    grant_a
)

#
# Lease B
#
grant_b = central.call(
    "grant",
    node_id="B",
)

node_b.grant(
    grant_b
)

print(
    "A EPOCH =",
    node_a.epoch,
)

print(
    "B EPOCH =",
    node_b.epoch,
)


#
# Snapshot
#
snapshot = central.call(
    "snapshot"
)

sync_a = node_a.sync(
    snapshot
)

activate = central.call(
    "activate",
    node_id="A",
    ready=sync_a,
    ttl=30,
)

print("\n=== ACTIVATE ===")
pprint(activate)

#
# B syncs same snapshot
#
node_b.sync(
    snapshot
)

proposal = {
    "operation_id": "move-test-004",
    "epoch": node_a.epoch,
    "ruleset": RULESET,
    "base_version": snapshot["version"],
    "base_hash": snapshot["state_hash"],
    "base": snapshot["state"],
    "commands": [
        {
            "seq": 1,
            "actor": "p1",
            "dx": 1,
            "dy": 0,
        }
    ],
}

proposal["input_hash"] = fingerprint(
    proposal["commands"]
)

prepare = central.call(
    "prepare",
    proposal=proposal,
)

print("\n=== PREPARE ===")
pprint(prepare)

#
# A result
#
result_a = node_a.run(
    proposal
)

submit_a = central.call(
    "result",
    node_id="A",
    result=result_a,
)

print("\n=== RESULT A ===")
pprint(submit_a)

#
# B result
#
result_b = node_b.run(
    proposal
)

submit_b = central.call(
    "result",
    node_id="B",
    result=result_b,
)

print("\n=== RESULT B ===")
pprint(submit_b)

#
# Status
#
status = central.call(
    "status",
    operation_id="move-test-004",
)

print("\n=== STATUS ===")
pprint(status)