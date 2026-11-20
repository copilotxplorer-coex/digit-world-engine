from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode

central = HttpPeer(
    "http://157.85.96.133:9001"
)

node = RegionNode(
    "pc-node-a",
    b"A" * 32,
)

central.call(
    "register",
    node_id="pc-node-a",
)

grant = central.call(
    "grant",
    node_id="pc-node-a",
)

print("GRANT =", grant)

node.grant(grant)

print("NODE EPOCH =", node.epoch)