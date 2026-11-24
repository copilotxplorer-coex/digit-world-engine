import json
import time

work_queue = []
work_results = {}

work_status = {}
assigned_work = {}

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
                if action == "heartbeat":
                    node_id = body["node_id"]

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
                            },
                        },
                    )

                #
                # GRANT
                #
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
                if action == "poll_work":

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
                            body["node_id"],
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
                if action == "submit_work":

                    work_id = body["work_id"]

                    result = body["result"]

                    #
                    # find original work
                    #
                    original_work = assigned_work.get(
                        work_id
                    )

                    #
                    # validate move result
                    #
                    if (
                        original_work
                        and original_work.get("kind")
                        == "move"
                    ):

                        ok = validate_move_result(
                            original_work,
                            result,
                        )

                        if not ok:

                            return self.reply(
                                400,
                                {
                                    "ok": False,
                                    "error":
                                        "Validation failed",
                                },
                            )

                    work_results[work_id] = {
                        "node_id":
                            body["node_id"],
                        "result":
                            result,
                    }

                    work_status[
                        work_id
                    ] = {
                        "status": "completed",
                        "node_id":
                            body["node_id"],
                    }

                    return self.reply(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "accepted": True,
                                "work_id":
                                    work_id,
                            },
                        },
                    )

                #
                # get_work_result
                #
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
