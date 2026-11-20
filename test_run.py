from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode

from region_runtime.contract import (
    fingerprint,
    RULESET,
)



central = HttpPeer(
    "http://157.85.96.133:9001"
)

#
# Register
#
central.call(
    "register",
    node_id="pc-node-a",
)

#
# Get lease
#
grant = central.call(
    "grant",
    node_id="pc-node-a",
)

#
# Create real node
#
node = RegionNode(
    "pc-node-a",
    b"A" * 32,
)

#
# Activate lease
#
node.grant(grant)

print(
    "NODE EPOCH =",
    node.epoch,
)

#
# Pull snapshot from central
#
snapshot = central.call(
    "snapshot"
)

print(
    "SNAPSHOT VERSION =",
    snapshot["version"],
)

#
# Sync replica
#
receipt = node.sync(
    snapshot
)

print(
    "SYNC RECEIPT =",
    receipt,
)

#
# Build proposal locally
#
proposal = {
    "operation_id": "move-test-001",
    "epoch": node.epoch,
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

#
# input hash
#
from region_runtime.contract import fingerprint

proposal["input_hash"] = fingerprint(
    proposal["commands"]
)

#
# run simulation
#
result = node.run(
    proposal
)

print(
    "RUN RESULT ="
)

print(
    result
)