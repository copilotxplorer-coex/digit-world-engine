from pprint import pprint

from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode

CENTRAL_URL = "http://157.85.96.133:9001"

OPERATION_ID = "move-test-009"

central = HttpPeer(
    CENTRAL_URL
)

#
# Load recovered operation
#
recovery = central.call(
    "recovery",
    operation_id=OPERATION_ID,
)

print("\n=== RECOVERY ===")
pprint(recovery)

proposal = recovery["proposal"]

#
# Replica C
#
node_c = RegionNode(
    "C",
    b"C" * 32,
)

#
# Register C
#
central.call(
    "register",
    node_id="C",
)

#
# Current snapshot
#
snapshot = central.call(
    "snapshot"
)

node_c.sync(
    snapshot
)

#
# Lease from proposal epoch
#
recovery_epoch = proposal["epoch"]

from region_runtime.contract import signed

lease_c = signed(
    b"C" * 32,
    {
        "node_id": "C",
        "epoch": recovery_epoch,
        "ttl": 30,
    },
)

node_c.grant(
    lease_c
)

print(
    "\nNODE C EPOCH =",
    node_c.epoch,
)

print(
    "PROPOSAL EPOCH =",
    proposal["epoch"],
)

#
# Replica execution
#
result_c = node_c.run(
    proposal
)

print("\n=== RESULT C ===")
pprint(result_c)

#
# Submit receipt
#
submit_c = central.call(
    "result",
    node_id="C",
    result=result_c,
)

print("\n=== SUBMIT C ===")
pprint(submit_c)

#
# Check operation
#
status = central.call(
    "status",
    operation_id=OPERATION_ID,
)

print("\n=== STATUS ===")
pprint(status)