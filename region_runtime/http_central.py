import json
import time
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
