from pprint import pprint

from region_runtime.http_peer import HttpPeer

central = HttpPeer(
    "http://157.85.96.133:9001"
)

result = central.call(
    "takeover",
    operation_id="move-test-008",
    node_id="B",
)

pprint(result)