from region_runtime.http_peer import HttpPeer

central = HttpPeer(
    "http://157.85.96.133:9001"
)

print(
    "register:",
    central.call(
        "register",
        node_id="pc-node-a",
    ),
)

print(
    "heartbeat:",
    central.call(
        "heartbeat",
        node_id="pc-node-a",
    ),
)

print(
    "grant:",
    central.call(
        "grant",
        node_id="pc-node-a",
    ),
)

print(
    "snapshot:",
    central.call(
        "snapshot",
    ),
)

print(
    "nodes:",
    central.call(
        "nodes",
    ),
)