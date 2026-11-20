from pprint import pprint

from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode
from region_runtime.contract import (
    RULESET,
    fingerprint,
    signed,
)

CENTRAL_URL = "http://157.85.96.133:9001"

VERIFIER_KEY = b"V" * 32

OPERATION_ID = "move-test-005"

#
# Central
#
central = HttpPeer(
    CENTRAL_URL
)

#
# Nodes
#
node_a = RegionNode(
    "A",
    b"A" * 32,
)

node_b = RegionNode(
    "B",
    b"B" * 32,
)

print("\n=== REGISTER ===")

pprint(
    central.call(
        "register",
        node_id="A",
    )
)

pprint(
    central.call(
        "register",
        node_id="B",
    )
)

#
# Lease
#
print("\n=== GRANT ===")

grant_a = central.call(
    "grant",
    node_id="A",
)

grant_b = central.call(
    "grant",
    node_id="B",
)

node_a.grant(
    grant_a
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

#
# Sync A
#
sync_a = node_a.sync(
    snapshot
)

#
# Sync B
#
sync_b = node_b.sync(
    snapshot
)

print("\n=== ACTIVATE ===")

activate = central.call(
    "activate",
    node_id="A",
    ready=sync_a,
    ttl=30,
)

pprint(activate)

#
# Proposal
#
proposal = {
    "operation_id": OPERATION_ID,
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

print("\n=== PREPARE ===")

prepare = central.call(
    "prepare",
    proposal=proposal,
)

pprint(prepare)

#
# Node A
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
# Node B
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
# Verifier
#
verify_envelope = signed(
    VERIFIER_KEY,
    {
        "operation_id": result_a["body"]["operation_id"],
        "epoch": result_a["body"]["epoch"],
        "base_version": result_a["body"]["base_version"],
        "base_hash": result_a["body"]["base_hash"],
        "input_hash": result_a["body"]["input_hash"],
        "ruleset": result_a["body"]["ruleset"],
        "state": result_a["body"]["state"],
        "state_hash": result_a["body"]["state_hash"],
    },
)

print("\n=== VERIFY ===")

verify_result = central.call(
    "verify",
    operation_id=OPERATION_ID,
    envelope=verify_envelope,
)

pprint(verify_result)

#
# Commit
#
print("\n=== COMMIT ===")

commit_result = central.call(
    "commit",
    operation_id=OPERATION_ID,
)

pprint(commit_result)

#
# Final Status
#
print("\n=== FINAL STATUS ===")

status = central.call(
    "status",
    operation_id=OPERATION_ID,
)

pprint(status)