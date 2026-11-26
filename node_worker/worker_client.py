import secrets
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )


from region_runtime.http_peer import HttpPeer


#
# Development fallback only.
#
# Production will replace this with:
# https://game.2in.xyz/worker/
#
DEFAULT_CENTRAL_URL = (
    "http://157.85.96.133:9001"
)

DEFAULT_NODE_ID = "PC-WORKER-01"


class WorkerClient:

    def __init__(
        self,
        *,
        central_url=DEFAULT_CENTRAL_URL,
        node_id=DEFAULT_NODE_ID,
        poll_interval=0.5,
        worker_ticket=None,
        require_ticket=False,
        peer=None,
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

        if len(node_id) > 128:
            raise ValueError(
                "node_id is too long"
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

        if worker_ticket is not None:
            if not isinstance(
                worker_ticket,
                str,
            ):
                raise TypeError(
                    "worker_ticket must be a string"
                )

            if (
                not worker_ticket
                or len(worker_ticket) > 4096
            ):
                raise ValueError(
                    "Invalid worker_ticket"
                )

        if not isinstance(
            require_ticket,
            bool,
        ):
            raise TypeError(
                "require_ticket must be bool"
            )

        if (
            require_ticket
            and not worker_ticket
        ):
            raise ValueError(
                "worker_ticket is required"
            )

        self.central_url = central_url
        self.node_id = node_id
        self.poll_interval = float(
            poll_interval
        )

        self.require_ticket = (
            require_ticket
        )

        #
        # Ticket exists in memory only until attach succeeds.
        # Never print or include it in exceptions.
        #
        self._worker_ticket = (
            worker_ticket
        )

        self._worker_session_token = None
        self._worker_session_id = None
        self._worker_identity = None

        self.central = (
            peer
            if peer is not None
            else HttpPeer(
                self.central_url
            )
        )

        self._stop_event = (
            threading.Event()
        )

        self._thread = None
        self._registered = False
        self._attached = False
        self._last_error = None

    @property
    def running(self):
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    @property
    def attached(self):
        return self._attached

    @property
    def worker_identity(self):
        return self._worker_identity

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

        if (
            self.require_ticket
            and not self._worker_ticket
            and not self._worker_session_token
        ):
            raise ValueError(
                "worker_ticket is required"
            )

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
            "ticket_mode": (
                self._worker_ticket is not None
                or self._worker_session_token
                is not None
            ),
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

        #
        # Clear local credentials on shutdown.
        #
        self._worker_ticket = None
        self._worker_session_token = None
        self._worker_session_id = None
        self._worker_identity = None
        self._attached = False
        self._registered = False

        return {
            "stopped": stopped,
            "node_id": self.node_id,
        }

    def _session_fields(self):
        if not self._worker_session_token:
            if self.require_ticket:
                raise RuntimeError(
                    "Worker session is not attached"
                )

            return {}

        return {
            "worker_session_token":
                self._worker_session_token,
        }

    def _attach(self):
        ticket = self._worker_ticket

        if not ticket:
            if self.require_ticket:
                raise RuntimeError(
                    "Worker ticket is unavailable"
                )

            return None

        #
        # A fresh nonce prevents a captured attach response
        # from being reused as another client request.
        #
        client_nonce = secrets.token_urlsafe(
            24
        )

        result = self.central.call(
            "worker_attach",
            node_id=self.node_id,
            worker_ticket=ticket,
            client_nonce=client_nonce,
        )

        if not isinstance(
            result,
            dict,
        ):
            raise ValueError(
                "Invalid worker attach response"
            )

        session_token = result.get(
            "worker_session_token"
        )

        session_id = result.get(
            "worker_session_id"
        )

        identity = result.get(
            "worker_identity"
        )

        if (
            not isinstance(
                session_token,
                str,
            )
            or not session_token
            or len(session_token) > 512
        ):
            raise ValueError(
                "Invalid worker session token"
            )

        if (
            not isinstance(
                session_id,
                str,
            )
            or not session_id
            or len(session_id) > 128
        ):
            raise ValueError(
                "Invalid worker session id"
            )

        if (
            not isinstance(
                identity,
                str,
            )
            or not identity
            or len(identity) > 128
        ):
            raise ValueError(
                "Invalid worker identity"
            )

        self._worker_session_token = (
            session_token
        )

        self._worker_session_id = (
            session_id
        )

        self._worker_identity = (
            identity
        )

        self._attached = True

        #
        # One-time Ticket is removed immediately after attach.
        #
        self._worker_ticket = None

        print(
            (
                "WORKER ATTACHED "
                f"node_id={self.node_id}"
            ),
            flush=True,
        )

        return result

    def _register_legacy(self):
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

        return result

    def _connect(self):
        if self._worker_session_token:
            self._attached = True
            return

        if self._worker_ticket:
            self._attach()
            return

        if self.require_ticket:
            raise RuntimeError(
                "Worker ticket is required"
            )

        self._register_legacy()

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
        session_fields = (
            self._session_fields()
        )

        self.central.call(
            "heartbeat",
            node_id=self.node_id,
            **session_fields,
        )

        work = self.central.call(
            "poll_work",
            node_id=self.node_id,
            **session_fields,
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
                **session_fields,
            )
        )

        print(
            "WORKER SUBMIT:",
            submit_result,
            flush=True,
        )

    def _reset_connection_state(
        self,
    ):
        if self.require_ticket:
            #
            # A Ticket is one-time-use. Never retry attach
            # automatically with a consumed Ticket.
            #
            self._attached = False
            self._worker_session_token = None
            self._worker_session_id = None
            self._worker_identity = None
        else:
            self._registered = False

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
                if (
                    not self._registered
                    and not self._attached
                ):
                    self._connect()

                self._run_once()

                self._last_error = None

            except Exception as exc:
                self._last_error = exc

                print(
                    "WORKER ERROR:",
                    repr(exc),
                    flush=True,
                )

                self._reset_connection_state()

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
