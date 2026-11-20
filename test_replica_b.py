from pprint import pprint

from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode

CENTRAL_URL = "http://157.85.96.133:9001"

central = HttpPeer(
    CENTRAL_URL
)

snapshot = central.call(
    "snapshot"
)

node_b = RegionNode(
    "B",
    b"B" * 32
)

#
# ใช้ lease เดียวกับ operation เดิม
#
grant = {
    "body": {
        "node_id": "B",
        "epoch": 13,
        "ttl": 30,
    },
    "proof": None,
}

#
# ดึง grant ใหม่จาก Central ดีกว่า
#

central.call(
    "register",
    node_id="B"
)

grant = central.call(
    "grant",
    node_id="B"
)

node_b.grant(
    grant
)


print(
    "NODE B EPOCH =",
    node_b.epoch,
)

node_b.sync(
    snapshot
)

proposal = central.call(
    "status",
    operation_id="move-test-002"
)["proposal"]


print(
    "PROPOSAL EPOCH =",
    proposal["epoch"],
)

result_b = node_b.run(
    proposal
)

print("\n=== RESULT B ===")
pprint(result_b)

response = central.call(
    "result",
    node_id="B",
    result=result_b,
)

print(
    "PROPOSAL RULESET =",
    proposal["ruleset"],
)

print("\n=== CENTRAL RESPONSE ===")
pprint(response)

status = central.call(
    "status",
    operation_id="move-test-002"
)

print("\n=== STATUS ===")
pprint(status)