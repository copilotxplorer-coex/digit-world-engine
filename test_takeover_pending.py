from pprint import pprint

from region_runtime.http_peer import HttpPeer

CENTRAL_URL = "http://157.85.96.133:9001"

central = HttpPeer(
    CENTRAL_URL
)

print("\n=== BEFORE TAKEOVER ===")

before = central.call(
    "status",
    operation_id="move-test-008",
)

pprint(before)

print("\n=== TAKEOVER ===")

takeover = central.call(
    "takeover",
    operation_id="move-test-008",
    node_id="B",
)

pprint(takeover)

print("\n=== AFTER TAKEOVER ===")

after = central.call(
    "status",
    operation_id="move-test-008",
)

pprint(after)