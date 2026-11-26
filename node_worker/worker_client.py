import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from region_runtime.http_peer import HttpPeer


DEFAULT_CENTRAL_URL = "http://157.85.96.133:9001"

DEFAULT_NODE_ID = "PC-WORKER-01"


class WorkerClient:

    def __init__(
        self,
        *,
        central_url=DEFAULT_CENTRAL_URL,
        node_id=DEFAULT_NODE_ID,
        poll_interval=0.5,
    ):
        if not isinstance(
            central_url,
            str,
        ):
            raise TypeError(
                "central_url must be a string"
            )

        if not central_url:
            raise ValueError(
                "central_url is required"
            )

        if not isinstance(
            node_id,
            str,
        ):
            raise TypeError(
                "node_id must be a string"
            )

        if not node_id:
            raise ValueError(
                "node_id is required"
            )

        if not isinstance(
            poll_interval,
            (int, float),
        ):
            raise TypeError(
                "poll_interval must be numeric"
            )

        if (
            poll_interval <= 0
            or poll_interval > 60
        ):
            raise ValueError(
                "poll_interval must be > 0 and <= 60"
            )

        self.central_url = central_url
        self.node_id = node_id
        self.poll_interval = float(
            poll_interval
        )

        self.central = HttpPeer(
            self.central_url
        )

        self._stop_event = (
            threading.Event()
        )

        self._thread = None

        self._registered = False

        self._last_error = None

    @property
    def running(self):
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    @property
    def last_error(self):
        return self._last_error

    def start(self):
        if self.running:
            return {
                "started": False,
                "reason": "already-running",
                "node_id": self.node_id,
            }

        self._stop_event.clear()

        self._last_error = None

        self._thread = threading.Thread(
            target=self._run,
            name=(
                "digit-world-worker-"
                + self.node_id
            ),
            daemon=True,
        )

        self._thread.start()

        return {
            "started": True,
            "node_id": self.node_id,
        }

    def stop(
        self,
        timeout=5,
    ):
        self._stop_event.set()

        thread = self._thread

        if (
            thread is not None
            and thread.is_alive()
        ):
            thread.join(
                timeout=timeout
            )

        stopped = not self.running

        if stopped:
            self._thread = None

        return {
            "stopped": stopped,
            "node_id": self.node_id,
        }

    def _register(self):
        result = self.central.call(
            "register",
            node_id=self.node_id,
        )

        self._registered = True

        print(
            "WORKER REGISTER:",
            result,
            flush=True,
        )

    def _execute_move(
        self,
        work,
    ):
        payload = work.get(
            "payload",
            {},
        )

        required = (
            "x",
            "y",
            "dx",
            "dy",
        )

        missing = [
            name
            for name in required
            if name not in payload
        ]

        if missing:
            return {
                "approved": False,
                "error": (
                    "missing move fields: "
                    + ", ".join(missing)
                ),
            }

        started_at = time.monotonic()

        result = {
            "approved": True,
            "new_x": (
                payload["x"]
                + payload["dx"]
            ),
            "new_y": (
                payload["y"]
                + payload["dy"]
            ),
        }

        elapsed = (
            time.monotonic()
            - started_at
        )

        print(
            (
                "WORKER EXECUTE "
                f"work_id={work['work_id']} "
                f"kind=move "
                f"elapsed={elapsed:.6f}s"
            ),
            flush=True,
        )

        return result

    def _execute(
        self,
        work,
    ):
        kind = work.get(
            "kind"
        )

        if kind == "move":
            return self._execute_move(
                work
            )

        return {
            "approved": False,
            "error": (
                "unsupported work type: "
                f"{kind}"
            ),
        }

    def _run_once(self):
        self.central.call(
            "heartbeat",
            node_id=self.node_id,
        )

        work = self.central.call(
            "poll_work",
            node_id=self.node_id,
        )

        if not work:
            return

        print(
            "WORKER WORK:",
            work,
            flush=True,
        )

        work_id = work.get(
            "work_id"
        )

        if not work_id:
            raise ValueError(
                "work_id is required"
            )

        result = self._execute(
            work
        )

        submit_result = (
            self.central.call(
                "submit_work",
                node_id=self.node_id,
                work_id=work_id,
                result=result,
            )
        )

        print(
            "WORKER SUBMIT:",
            submit_result,
            flush=True,
        )

    def _run(self):
        print(
            (
                "WORKER START "
                f"node_id={self.node_id}"
            ),
            flush=True,
        )

        while not self._stop_event.is_set():
            try:
                if not self._registered:
                    self._register()

                self._run_once()

                self._last_error = None

            except Exception as exc:
                self._last_error = exc

                print(
                    "WORKER ERROR:",
                    repr(exc),
                    flush=True,
                )

                # Register ใหม่หลัง connection
                # หรือ Central restart
                self._registered = False

            self._stop_event.wait(
                self.poll_interval
            )

        print(
            (
                "WORKER STOP "
                f"node_id={self.node_id}"
            ),
            flush=True,
        )