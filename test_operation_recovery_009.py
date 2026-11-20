from pprint import pprint

from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode
from region_runtime.contract import (
    RULESET,
    fingerprint,
    signed,
)

CENTRAL_URL = "http://157.85.96.133:9001"

OPERATION_ID = "move-test-011"

VERIFIER_KEY = b"V" * 32

#
# Central
#
central = HttpPeer(
    CENTRAL_URL
)

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

print("\n=== REGISTER ===")

central.call(
    "register",
    node_id="A",
)

central.call(
    "register",
    node_id="B",
)

#
# Initial lease
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

node_b.sync(
    snapshot
)

#
# Activate
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
# Leader starts work
#
result_a = node_a.run(
    proposal
)

print("\n=== NODE A CRASH ===")
print("Result generated but NOT submitted")

#
# Recovery lookup
#
recovery = central.call(
    "recovery",
    operation_id=OPERATION_ID,
)

print("\n=== RECOVERY BEFORE TAKEOVER ===")
pprint(recovery)

#
# Takeover
#
takeover = central.call(
    "takeover",
    operation_id=OPERATION_ID,
    node_id="B",
)

print("\n=== TAKEOVER ===")
pprint(takeover)

#
# Recovery after takeover
#
recovery = central.call(
    "recovery",
    operation_id=OPERATION_ID,
)

print("\n=== RECOVERY AFTER TAKEOVER ===")
pprint(recovery)

proposal = recovery["proposal"]

print(
    "\nPROPOSAL EPOCH AFTER TAKEOVER =",
    proposal["epoch"],
)

#
# Recovery lease from Central
#
recovery_lease = recovery[
    "recovery_lease"
]

print("\n=== RECOVERY LEASE ===")
pprint(
    recovery_lease
)

#
# IMPORTANT
# use recovery lease
# do NOT call grant() again
#
node_b = RegionNode(
    "B",
    b"B" * 32,
)

node_b.grant(
    recovery_lease
)

print(
    "NODE B EPOCH =",
    node_b.epoch,
)

#
# Sync latest snapshot
#
snapshot = central.call(
    "snapshot"
)

node_b.sync(
    snapshot
)

#
# Execute recovered operation
#
print("\n=== RUN RECOVERED OPERATION ===")

result_b = node_b.run(
    proposal
)

pprint(
    result_b
)

#
# Submit result
#
submit_b = central.call(
    "result",
    node_id="B",
    result=result_b,
)

print("\n=== SUBMIT B ===")
pprint(
    submit_b
)

#
# Verifier receipt
#
verify_envelope = signed(
    VERIFIER_KEY,
    {
        "operation_id":
            result_b["body"]["operation_id"],
        "epoch":
            result_b["body"]["epoch"],
        "base_version":
            result_b["body"]["base_version"],
        "base_hash":
            result_b["body"]["base_hash"],
        "input_hash":
            result_b["body"]["input_hash"],
        "ruleset":
            result_b["body"]["ruleset"],
        "state":
            result_b["body"]["state"],
        "state_hash":
            result_b["body"]["state_hash"],
    },
)

#
# Verify
#
verify = central.call(
    "verify",
    operation_id=OPERATION_ID,
    envelope=verify_envelope,
)

print("\n=== VERIFY ===")
pprint(
    verify
)

#
# Commit
#
commit = central.call(
    "commit",
    operation_id=OPERATION_ID,
)

print("\n=== COMMIT ===")
pprint(
    commit
)

#
# Final status
#
status = central.call(
    "status",
    operation_id=OPERATION_ID,
)

print("\n=== FINAL STATUS ===")
pprint(
    status
)