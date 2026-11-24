import json
import time
import uuid
import urllib.request


CENTRAL_URL = "http://127.0.0.1:9001"


class RuntimeBridge:

    def _call(
        self,
        action,
        **kwargs,
    ):

        payload = {
            "action": action,
            **kwargs,
        }

        req = urllib.request.Request(
            CENTRAL_URL,
            data=json.dumps(
                payload
            ).encode(
                "utf-8"
            ),
            headers={
                "Content-Type":
                    "application/json",
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=10,
        ) as response:

            data = json.loads(
                response.read()
            )

        return data["result"]

    def move(
        self,
        player_id,
        x,
        y,
        dx,
        dy,
    ):

        work_id = str(
            uuid.uuid4()
        )

        print(
            f"QUEUE MOVE work_id={work_id}",
            flush=True,
        )

        self._call(
            "enqueue_work",
            work={
                "work_id": work_id,
                "kind": "move",
                "payload": {
                    "player_id": player_id,
                    "x": x,
                    "y": y,
                    "dx": dx,
                    "dy": dy,
                },
            },
        )

        deadline = (
            time.time() + 30
        )

        print(
            f"WAIT RESULT work_id={work_id}",
            flush=True,
        )

        while (
            time.time()
            < deadline
        ):

            result = self._call(
                "get_work_result",
                work_id=work_id,
            )

            status = result.get(
                "status",
                {},
            )

            if (
                isinstance(
                    status,
                    dict,
                )
                and status.get(
                    "status"
                )
                == "completed"
            ):

                worker_result = (
                    result[
                        "result"
                    ][
                        "result"
                    ]
                )

                print(
                    f"RESULT OK work_id={work_id}",
                    flush=True,
                )

                return worker_result

            time.sleep(
                0.2
            )

        print(
            f"RESULT TIMEOUT work_id={work_id}",
            flush=True,
        )

        raise TimeoutError(
            "worker timeout"
        )
