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

OPERATION_ID = "move-test-006"

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
    "\nA EPOCH =",
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
# Sync both
#
sync_a = node_a.sync(
    snapshot
)

sync_b = node_b.sync(
    snapshot
)

#
# Activate Region
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
# Proposal
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
# Prepare
#
prepare = central.call(
    "prepare",
    proposal=proposal,
)

print("\n=== PREPARE ===")
pprint(prepare)

#
# Node A starts work
#
result_a = node_a.run(
    proposal
)

print("\n=== A RUN COMPLETE ===")
print(
    "Simulate Node A crash..."
)

#
# IMPORTANT:
# do NOT submit result_a
#

#
# Node B takes over
#
result_b = node_b.run(
    proposal
)

print("\n=== B TAKEOVER RESULT ===")
pprint(result_b)

submit_b = central.call(
    "result",
    node_id="B",
    result=result_b,
)

print("\n=== RESULT B ===")
pprint(submit_b)

#
# Verify
#
verify_envelope = signed(
    VERIFIER_KEY,
    {
        "operation_id": result_b["body"]["operation_id"],
        "epoch": result_b["body"]["epoch"],
        "base_version": result_b["body"]["base_version"],
        "base_hash": result_b["body"]["base_hash"],
        "input_hash": result_b["body"]["input_hash"],
        "ruleset": result_b["body"]["ruleset"],
        "state": result_b["body"]["state"],
        "state_hash": result_b["body"]["state_hash"],
    },
)

verify = central.call(
    "verify",
    operation_id=OPERATION_ID,
    envelope=verify_envelope,
)

print("\n=== VERIFY ===")
pprint(verify)

#
# Commit
#
commit = central.call(
    "commit",
    operation_id=OPERATION_ID,
)

print("\n=== COMMIT ===")
pprint(commit)

status = central.call(
    "status",
    operation_id=OPERATION_ID,
)

print("\n=== FINAL STATUS ===")
pprint(status)