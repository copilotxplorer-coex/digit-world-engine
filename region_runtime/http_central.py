import json
import os
import time

work_queue = []
work_results = {}

work_status = {}
assigned_work = {}

worker_rewards = {}

import math

def validate_move_result(
    work,
    result,
):

    payload = work["payload"]

    if not isinstance(
        result.get("approved"),
        bool,
    ):
        return False

    nx = result.get("new_x")
    ny = result.get("new_y")

    if not isinstance(
        nx,
        (int, float),
    ):
        return False

    if not isinstance(
        ny,
        (int, float),
    ):
        return False

    if not math.isfinite(nx):
        return False

    if not math.isfinite(ny):
        return False

    expected_x = (
        payload["x"]
        + payload["dx"]
    )

    expected_y = (
        payload["y"]
        + payload["dy"]
    )

    if nx != expected_x:
        return False

    if ny != expected_y:
        return False

    return True

from http.server import BaseHTTPRequestHandler, HTTPServer

from region_runtime.store import RegionStore
from region_runtime.contract import signed


from region_runtime.worker_sessions import WorkerSessionStore
class CentralApi(HTTPServer):
    allow_reuse_address = True


def serve(
    db_path,
    node_keys,
    verifier_key,
    host="0.0.0.0",
    port=9001,
):
    store = RegionStore(
        db_path,
        node_keys,
        verifier_key,
    )

    nodes = {}

    worker_session_store = None

    worker_ticket_secret = os.environ.get(
        "DIGIT_WORLD_WORKER_TICKET_SECRET"
    )

    worker_session_db = os.environ.get(
        "DIGIT_WORLD_WORKER_SESSION_DB",
        "/opt/digit-world/rr/worker_sessions.db",
    )

    if worker_ticket_secret:
        worker_session_store = WorkerSessionStore(
            worker_session_db,
            ticket_secret=worker_ticket_secret,
            lease_seconds=30,
        )

        print(
            "Persistent Worker sessions enabled: "
            + worker_session_db,
            flush=True,
        )
    else:
        print(
            "Persistent Worker sessions disabled: "
            "missing DIGIT_WORLD_WORKER_TICKET_SECRET",
            flush=True,
        )
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, payload):
            raw = json.dumps(payload, allow_nan=False).encode("utf-8")

            self.send_response(status)
            self.send_header(
                "Content-Type",
                "application/json",
            )
            self.send_header(
                "Content-Length",
                str(len(raw)),
            )
            self.end_headers()

            self.wfile.write(raw)

        def do_POST(self):
            try:
                size = int(
                    self.headers.get(
                        "Content-Length",
                        "0",
                    )
                )

                body = json.loads(
                    self.rfile.read(size)
                )

                print("REQUEST =", body, flush=True)

                action = body["action"]

                #
                # REGISTER
                #
                #
                # WORKER ATTACH
                #
                if action == "worker_attach":
                    if worker_session_store is None:
                        raise ValueError(
                            "Worker Ticket mode "
                            "is unavailable"
                        )

                    required_fields = {
                        "worker_ticket",
                        "node_id",
                        "client_nonce",
                    }

                    missing_fields = [
                        name
                        for name in required_fields
                        if name not in body
                    ]

                    if missing_fields:
                        raise ValueError(
                            "Missing Worker attach fields: "
                            + ", ".join(
                                sorted(
                                    missing_fields
                                )
                            )
                        )

                    response = (
                        worker_session_store.attach(
                            worker_ticket=body[
                                "worker_ticket"
                            ],
                            node_id=body[
                                "node_id"
                            ],
                            client_nonce=body[
                                "client_nonce"
                            ],
                        )
                    )

                    node_id = body[
                        "node_id"
                    ]

                    now = time.time()

                    nodes[node_id] = {
                        "registered": now,
                        "last_seen": now,
                        "ticket_mode": True,
                        "worker_identity":
                            response[
                                "worker_identity"
                            ],
                    }

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": response,
                        },
                    )

                if action == "register":
                    node_id = body["node_id"]

                    nodes[node_id] = {
                        "registered": time.time(),
                        "last_seen": time.time(),
                    }

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "accepted": True,
                                "node_id": node_id,
                            },
                        },
                    )

                #
                # HEARTBEAT
                #
                #
                # WORKER TICKET HEARTBEAT
                #
                if action == "heartbeat":
                    node_id = body[
                        "node_id"
                    ]

                    worker_token = body.get(
                        "worker_session_token"
                    )

                    if worker_token:
                        if worker_session_store is None:
                            raise ValueError(
                                "Worker Ticket mode "
                                "is unavailable"
                            )

                        session = (
                            worker_session_store.heartbeat(
                                worker_session_token=(
                                    worker_token
                                ),
                                node_id=node_id,
                            )
                        )

                        now = time.time()

                        existing_node = nodes.get(
                            node_id,
                            {},
                        )

                        nodes[node_id] = {
                            "registered":
                                existing_node.get(
                                    "registered",
                                    now,
                                ),
                            "last_seen": now,
                            "ticket_mode": True,
                            "worker_identity":
                                session[
                                    "player_id"
                                ],
                            "worker_session_id":
                                session[
                                    "worker_session_id"
                                ],
                        }

                        return self.reply(
                            200,
                            {
                                "ok": True,
                                "result": {
                                    "status": "alive",
                                    "node_id": node_id,
                                    "worker_identity":
                                        session[
                                            "player_id"
                                        ],
                                    "worker_session_id":
                                        session[
                                            "worker_session_id"
                                        ],
                                    "lease_expires_at":
                                        session[
                                            "lease_expires_at"
                                        ],
                                },
                            },
                        )

                    #
                    # Legacy development path.
                    # Remove after HTTPS Worker Gateway
                    # and Ticket mode pass production tests.
                    #
                    if node_id not in nodes:
                        raise ValueError(
                            "unknown node"
                        )

                    nodes[node_id][
                        "last_seen"
                    ] = time.time()

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "status": "alive",
                                "node_id": node_id,
                                "ticket_mode": False,
                            },
                        },
                    )

                if action == "grant":
                    node_id = body["node_id"]

                    if node_id not in nodes:
                        raise ValueError(
                            "unknown node"
                        )

                    snapshot = store.snapshot()

                    envelope = signed(
                        node_keys["A"],
                        {
                            "node_id": node_id,
                            "epoch": snapshot["epoch"] + 1,
                            "ttl": 30,
                        },
                    )

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": envelope,
                        },
                    )

                #
                # ACTIVATE
                #
                if action == "activate":
                    node_id = body["node_id"]
                    ready = body["ready"]
                    ttl = body.get("ttl", 30)

                    response = store.grant(
                        node_id,
                        ready,
                        ttl,
                    )

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": response,
                        },
                    )


                #
                # PREPARE
                #
                if action == "prepare":
                    proposal = body["proposal"]

                    response = store.prepare(
                        proposal["operation_id"],
                        proposal["commands"],
                    )

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": response,
                        },
                    )


                #
                # RESULT
                #
                if action == "result":
                    node_id = body["node_id"]
                    result = body["result"]

                    response = store.result(
                        node_id,
                        result,
                    )

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": response,
                        },
                    )

                #
                # VERIFY
                #
                if action == "verify":
                    proposal = body["proposal"]
                    results = body["results"]

                    response = store.verify(
                        proposal,
                        results,
                    )

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": response,
                        },
                    )

                #
                # COMMIT
                #
                if action == "commit":
                    operation_id = body["operation_id"]

                    response = store.commit(
                        operation_id,
                    )

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": response,
                        },
                    )


                #
                # STATUS
                #
                #
                # enqueue_work
                #
                if action == "enqueue_work":

                    work = body["work"]

                    work_queue.append(
                        work
                    )

                    work_status[
                        work["work_id"]
                    ] = {
                        "status": "queued"
                    }

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "queued": True,
                                "work_id":
                                    work["work_id"],
                            },
                        },
                    )

                #
                # poll_work
                #
                #
                # WORKER TICKET POLL
                #
                if action == "poll_work":
                    node_id = body[
                        "node_id"
                    ]

                    worker_token = body.get(
                        "worker_session_token"
                    )

                    worker_session = None

                    if worker_token:
                        if worker_session_store is None:
                            raise ValueError(
                                "Worker Ticket mode "
                                "is unavailable"
                            )

                        worker_session = (
                            worker_session_store.authenticate(
                                worker_session_token=(
                                    worker_token
                                ),
                                node_id=node_id,
                                renew=False,
                            )
                        )

                    else:
                        #
                        # Legacy migration path.
                        # Remove after HTTPS Worker Gateway
                        # passes production tests.
                        #
                        if node_id not in nodes:
                            raise ValueError(
                                "unknown node"
                            )

                    if not work_queue:

                        return self.reply(
                            200,
                            {
                                "ok": True,
                                "result": None,
                            },
                        )

                    work = work_queue.pop(0)

                    assigned_work[
                        work["work_id"]
                    ] = work

                    work_status[
                        work["work_id"]
                    ] = {
                        "status": "assigned",
                        "node_id":
                            node_id,
                        "worker_identity":
                            (
                                worker_session[
                                    "player_id"
                                ]
                                if worker_session
                                else None
                            ),
                        "worker_session_id":
                            (
                                worker_session[
                                    "worker_session_id"
                                ]
                                if worker_session
                                else None
                            ),
                    }

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": work,
                        },
                    )

                #
                # submit_work
                #
                #
                # WORKER TICKET SUBMIT
                #
                if action == "submit_work":
                    node_id = body[
                        "node_id"
                    ]

                    work_id = body[
                        "work_id"
                    ]

                    result = body[
                        "result"
                    ]

                    worker_token = body.get(
                        "worker_session_token"
                    )

                    worker_session = None

                    if worker_token:
                        if worker_session_store is None:
                            raise ValueError(
                                "Worker Ticket mode "
                                "is unavailable"
                            )

                        worker_session = (
                            worker_session_store.authenticate(
                                worker_session_token=(
                                    worker_token
                                ),
                                node_id=node_id,
                                renew=False,
                            )
                        )

                    else:
                        #
                        # Legacy migration path.
                        # Remove after HTTPS Worker Gateway
                        # passes production tests.
                        #
                        if node_id not in nodes:
                            raise ValueError(
                                "unknown node"
                            )

                    original_work = assigned_work.get(
                        work_id
                    )

                    if original_work is None:
                        raise ValueError(
                            "Unknown assigned work"
                        )

                    assignment = work_status.get(
                        work_id,
                        {},
                    )

                    if (
                        assignment.get(
                            "status"
                        )
                        != "assigned"
                    ):
                        raise ValueError(
                            "Work is not assigned"
                        )

                    if (
                        assignment.get(
                            "node_id"
                        )
                        != node_id
                    ):
                        raise ValueError(
                            "Assigned Worker mismatch"
                        )

                    if worker_session:
                        assigned_identity = (
                            assignment.get(
                                "worker_identity"
                            )
                        )

                        assigned_session_id = (
                            assignment.get(
                                "worker_session_id"
                            )
                        )

                        if (
                            assigned_identity
                            != worker_session[
                                "player_id"
                            ]
                        ):
                            raise ValueError(
                                "Assigned Worker "
                                "identity mismatch"
                            )

                        if (
                            assigned_session_id
                            != worker_session[
                                "worker_session_id"
                            ]
                        ):
                            raise ValueError(
                                "Assigned Worker "
                                "session mismatch"
                            )

                    #
                    # Validate untrusted Worker result.
                    #
                    if (
                        original_work.get(
                            "kind"
                        )
                        == "move"
                    ):
                        valid = validate_move_result(
                            original_work,
                            result,
                        )

                        if not valid:
                            return self.reply(
                                400,
                                {
                                    "ok": False,
                                    "error":
                                        "Validation failed",
                                },
                            )

                    persistent_reward = None

                    if worker_session:
                        persistent_reward = (
                            worker_session_store.credit_reward(
                                worker_session_token=(
                                    worker_token
                                ),
                                node_id=node_id,
                                work_id=work_id,
                                reward=1,
                            )
                        )
                    else:
                        #
                        # Legacy reward exists only during
                        # the migration period.
                        #
                        worker_rewards[node_id] = (
                            worker_rewards.get(
                                node_id,
                                0,
                            )
                            + 1
                        )

                    work_results[work_id] = {
                        "node_id": node_id,
                        "worker_identity":
                            (
                                worker_session[
                                    "player_id"
                                ]
                                if worker_session
                                else None
                            ),
                        "result": result,
                    }

                    work_status[work_id] = {
                        "status": "completed",
                        "node_id": node_id,
                        "worker_identity":
                            (
                                worker_session[
                                    "player_id"
                                ]
                                if worker_session
                                else None
                            ),
                        "worker_session_id":
                            (
                                worker_session[
                                    "worker_session_id"
                                ]
                                if worker_session
                                else None
                            ),
                    }

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "accepted": True,
                                "work_id": work_id,
                                "worker_identity":
                                    (
                                        worker_session[
                                            "player_id"
                                        ]
                                        if worker_session
                                        else None
                                    ),
                                "reward":
                                    persistent_reward,
                            },
                        },
                    )

                if action == "get_work_result":

                    work_id = body["work_id"]

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "status":
                                    work_status.get(
                                        work_id,
                                        {
                                            "status":
                                            "unknown"
                                        },
                                    ),
                                "result":
                                    work_results.get(
                                        work_id,
                                    ),
                            },
                        },
                    )


                #
                # worker_reward
                #
                #
                # WORKER TICKET DETACH
                #
                if action == "worker_detach":
                    if worker_session_store is None:
                        raise ValueError(
                            "Worker Ticket mode "
                            "is unavailable"
                        )

                    worker_token = body.get(
                        "worker_session_token"
                    )

                    if not worker_token:
                        raise ValueError(
                            "Worker session token "
                            "is required"
                        )

                    response = (
                        worker_session_store.detach(
                            worker_session_token=(
                                worker_token
                            ),
                            node_id=body.get(
                                "node_id"
                            ),
                        )
                    )

                    node_id = body.get(
                        "node_id"
                    )

                    if node_id:
                        nodes.pop(
                            node_id,
                            None,
                        )

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": response,
                        },
                    )

                #
                # WORKER TICKET REWARD
                #
                if action == "worker_reward":
                    node_id = body[
                        "node_id"
                    ]

                    worker_token = body.get(
                        "worker_session_token"
                    )

                    if worker_token:
                        if worker_session_store is None:
                            raise ValueError(
                                "Worker Ticket mode "
                                "is unavailable"
                            )

                        reward_result = (
                            worker_session_store.get_reward(
                                worker_session_token=(
                                    worker_token
                                ),
                                node_id=node_id,
                            )
                        )

                        return self.reply(
                            200,
                            {
                                "ok": True,
                                "result":
                                    reward_result,
                            },
                        )

                    #
                    # Legacy migration path.
                    # Remove after HTTPS Worker Gateway
                    # passes production tests.
                    #
                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "node_id":
                                    node_id,
                                "reward":
                                    worker_rewards.get(
                                        node_id,
                                        0,
                                    ),
                            },
                        },
                    )

                if action == "nodes":
                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": nodes,
                        },
                    )

                #
                # REGION STORE RPC
                #
                args = body.get(
                    "args",
                    [],
                )

                kwargs = body.get(
                    "kwargs",
                    {},
                )

                result = getattr(
                    store,
                    action,
                )(
                    *args,
                    **kwargs,
                )

                return self.reply(
                    200,
                    {
                        "ok": True,
                        "result": result,
                    },
                )

            except Exception as exc:
                print("ERROR =", exc, flush=True)
                return self.reply(
                    400,
                    {
                        "ok": False,
                        "error": str(exc),
                    },
                )

    server = CentralApi(
        (host, port),
        Handler,
    )

    print(
        f"Central API listening on {host}:{port}"
    )

    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    serve(
        db_path="/opt/digit-world/rr/region.db",
        node_keys={
            "A": b"A" * 32,
            "B": b"B" * 32,
            "C": b"C" * 32,
        },
        verifier_key=b"V" * 32,
        host="0.0.0.0",
        port=9001,
    )
