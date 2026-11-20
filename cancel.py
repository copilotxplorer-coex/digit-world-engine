from region_runtime.http_peer import HttpPeer

central = HttpPeer(
    "http://157.85.96.133:9001"
)

print(
    central.call(
        "cancel",
        operation_id="move-test-010"
    )
)