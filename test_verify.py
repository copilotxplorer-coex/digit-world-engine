from pprint import pprint

from region_runtime.http_peer import HttpPeer
from region_runtime.contract import signed

CENTRAL_URL = "http://157.85.96.133:9001"

VERIFIER_KEY = b"V" * 32

central = HttpPeer(
    CENTRAL_URL
)

status = central.call(
    "status",
    operation_id="move-test-004",
)

print("\n=== BEFORE VERIFY ===")
pprint(status)

proposal = status["proposal"]

#
# state สุดท้ายที่ A/B ส่งตรงกัน
#
final_state = {
    "positions": {
        "p1": [1, 0],
        "p2": [0, 3],
    },
    "seq": 1,
    "walls": [[2, 0]],
}

final_state_hash = (
    "846c11aca2ab55982734731fb2310200"
    "c13e00efcbd99106f579add3c08f074b"
)

envelope = signed(
    VERIFIER_KEY,
    {
        "operation_id": proposal["operation_id"],
        "epoch": proposal["epoch"],
        "base_version": proposal["base_version"],
        "base_hash": proposal["base_hash"],
        "input_hash": proposal["input_hash"],
        "ruleset": proposal["ruleset"],
        "state": final_state,
        "state_hash": final_state_hash,
    },
)

print("\n=== VERIFY ENVELOPE ===")
pprint(envelope)

verify_result = central.call(
    "verify",
    operation_id="move-test-004",
    envelope=envelope,
)

print("\n=== VERIFY RESULT ===")
pprint(verify_result)

status_after = central.call(
    "status",
    operation_id="move-test-004",
)

print("\n=== AFTER VERIFY ===")
pprint(status_after)
