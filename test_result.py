from pprint import pprint

from region_runtime.http_peer import HttpPeer
from region_runtime.node import RegionNode
from region_runtime.contract import (
    RULESET,
    fingerprint,
)

CENTRAL_URL = "http://157.85.96.133:9001"
NODE_ID = "A"

#
# Central
#
central = HttpPeer(
    CENTRAL_URL
)

#
# Region Node
#
node = RegionNode(
    NODE_ID,
    b"A" * 32,
)

#
# Register
#
print("\n=== REGISTER ===")

register_response = central.call(
    "register",
    node_id=NODE_ID,
)

pprint(register_response)

#
# Grant Lease
#
print("\n=== GRANT ===")

grant = central.call(
    "grant",
    node_id=NODE_ID,
)

pprint(grant)

node.grant(grant)

print(
    "NODE EPOCH =",
    node.epoch,
)

#
# Snapshot Sync
#
print("\n=== SNAPSHOT ===")

snapshot = central.call(
    "snapshot"
)

pprint(snapshot)

sync_receipt = node.sync(
    snapshot
)

print("\n=== SYNC RECEIPT ===")

pprint(sync_receipt)


activate_response = central.call(
    "activate",
    node_id=NODE_ID,
    ready=sync_receipt,
    ttl=30,
)

print("\n=== ACTIVATE RESPONSE ===")
print(activate_response)


#
# Proposal
#
proposal = {
    "operation_id": "move-test-002",
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

proposal["input_hash"] = fingerprint(
    proposal["commands"]
)

print("\n=== PROPOSAL ===")

pprint(proposal)


prepare_response = central.call(
    "prepare",
    proposal=proposal,
)

print(
    "\n=== PREPARE RESPONSE ==="
)

print(
    prepare_response
)



#
# Run
#

result = node.run(
    proposal
)

print("\n=== RUN RESULT ===")

pprint(result)

#
# Submit Result
#
response = central.call(
    "result",
    node_id=NODE_ID,
    result=result,
)

print("\n=== CENTRAL RESPONSE ===")

pprint(response)

#
# Current Node Registry
#
print("\n=== NODES ===")

pprint(
    central.call("nodes")
)