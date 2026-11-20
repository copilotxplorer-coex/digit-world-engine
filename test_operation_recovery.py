from pprint import pprint

from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode
from region_runtime.contract import (
    signed,
)

CENTRAL_URL = "http://157.85.96.133:9001"

OPERATION_ID = "move-test-008"

VERIFIER_KEY = b"V" * 32

#
# Central
#
central = HttpPeer(
    CENTRAL_URL
)

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
# Node B
#
node_b = RegionNode(
    "B",
    b"B" * 32,
)


#
# Recovery Lease Rebinding
#
recovery_lease = recovery[
    "recovery_lease"
]

print(
    "\n=== RECOVERY LEASE ==="
)

pprint(
    recovery_lease
)

node_b.grant(
    recovery_lease
)

print(
    "NODE B EPOCH =",
    node_b.epoch,
)


print(
    "NODE B EPOCH =",
    node_b.epoch,
)

#
# Sync current world snapshot
#
snapshot = central.call(
    "snapshot"
)

node_b.sync(
    snapshot
)

#
# Run recovered operation
#
print("\n=== RUN RECOVERED OPERATION ===")

result_b = node_b.run(
    proposal
)

pprint(result_b)

#
# Submit result
#
submit_b = central.call(
    "result",
    node_id="B",
    result=result_b,
)

print("\n=== SUBMIT B ===")
pprint(submit_b)

#
# Verifier receipt
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

#
# Verify
#
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

#
# Final status
#
status = central.call(
    "status",
    operation_id=OPERATION_ID,
)

print("\n=== FINAL STATUS ===")
pprint(status)