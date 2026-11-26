"""
Persistent Worker identity, lease and reward storage.

Security properties:
- Worker Ticket is verified before attach.
- Every Ticket ID can be consumed only once.
- One active Worker session is allowed per player identity.
- Worker session tokens are stored as SHA-256 digests only.
- Reward is credited once per work_id.
- Session and reward state survive process restarts.
"""

import hashlib
import hmac
import math
import secrets
import sqlite3
import time

from digit_world.worker_tickets import (
    WorkerTicketError,
    verify_worker_ticket,
)


DEFAULT_LEASE_SECONDS = 30

MIN_LEASE_SECONDS = 10

MAX_LEASE_SECONDS = 300


class WorkerSessionError(
    ValueError
):
    """Base Worker session error."""


class WorkerSessionActiveError(
    WorkerSessionError
):
    """The identity already owns an active Worker session."""


class WorkerSessionAuthError(
    WorkerSessionError
):
    """Worker session authentication failed."""


class WorkerTicketUsedError(
    WorkerSessionError
):
    """The one-time Worker Ticket has already been consumed."""


def _token_digest(
    token,
):
    if not isinstance(
        token,
        str,
    ):
        raise WorkerSessionAuthError(
            "Invalid Worker session token"
        )

    if (
        not token
        or len(token) > 512
    ):
        raise WorkerSessionAuthError(
            "Invalid Worker session token"
        )

    return hashlib.sha256(
        token.encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_node_id(
    node_id,
):
    if not isinstance(
        node_id,
        str,
    ):
        raise TypeError(
            "node_id must be a string"
        )

    node_id = node_id.strip()

    if not node_id:
        raise ValueError(
            "node_id is required"
        )

    if len(node_id) > 128:
        raise ValueError(
            "node_id is too long"
        )

    return node_id


def _validate_work_id(
    work_id,
):
    if not isinstance(
        work_id,
        str,
    ):
        raise TypeError(
            "work_id must be a string"
        )

    work_id = work_id.strip()

    if not work_id:
        raise ValueError(
            "work_id is required"
        )

    if len(work_id) > 256:
        raise ValueError(
            "work_id is too long"
        )

    return work_id


def _validate_time(
    value,
    *,
    name,
):
    if not isinstance(
        value,
        (int, float),
    ):
        raise TypeError(
            f"{name} must be numeric"
        )

    if not math.isfinite(
        value
    ):
        raise ValueError(
            f"{name} must be finite"
        )

    return float(
        value
    )


class WorkerSessionStore:

    def __init__(
        self,
        db_path,
        *,
        ticket_secret,
        lease_seconds=(
            DEFAULT_LEASE_SECONDS
        ),
        clock=None,
    ):
        if not isinstance(
            db_path,
            (str, bytes),
        ):
            raise TypeError(
                "db_path must be a path string"
            )

        if not db_path:
            raise ValueError(
                "db_path is required"
            )

        if not isinstance(
            lease_seconds,
            int,
        ):
            raise TypeError(
                "lease_seconds must be an integer"
            )

        if not (
            MIN_LEASE_SECONDS
            <= lease_seconds
            <= MAX_LEASE_SECONDS
        ):
            raise ValueError(
                "lease_seconds is outside "
                "the allowed range"
            )

        if isinstance(
            ticket_secret,
            str,
        ):
            ticket_secret = (
                ticket_secret.encode(
                    "utf-8"
                )
            )

        if not isinstance(
            ticket_secret,
            bytes,
        ):
            raise TypeError(
                "ticket_secret must be "
                "bytes or string"
            )

        if len(ticket_secret) < 32:
            raise ValueError(
                "ticket_secret must contain "
                "at least 32 bytes"
            )

        self.db_path = db_path
        self.ticket_secret = (
            ticket_secret
        )
        self.lease_seconds = (
            lease_seconds
        )
        self.clock = (
            clock
            if clock is not None
            else time.time
        )

        self._create_tables()

    def _connect(
        self,
    ):
        db = sqlite3.connect(
            self.db_path,
            timeout=10,
            isolation_level=None,
        )

        db.row_factory = (
            sqlite3.Row
        )

        db.execute(
            "PRAGMA foreign_keys=ON"
        )

        db.execute(
            "PRAGMA busy_timeout=10000"
        )

        return db

    def _create_tables(
        self,
    ):
        db = self._connect()

        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS
                worker_sessions (
                    player_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE,
                    token_digest TEXT NOT NULL UNIQUE,
                    node_id TEXT NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    attached_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                worker_sessions_lease_idx
                ON worker_sessions(
                    lease_expires_at
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS
                used_worker_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    player_id TEXT NOT NULL,
                    used_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                used_worker_tickets_expiry_idx
                ON used_worker_tickets(
                    expires_at
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS
                worker_rewards (
                    player_id TEXT PRIMARY KEY,
                    reward INTEGER NOT NULL
                        DEFAULT 0,
                    accepted_work_count INTEGER
                        NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS
                worker_reward_ledger (
                    work_id TEXT PRIMARY KEY,
                    player_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    reward INTEGER NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                worker_reward_ledger_player_idx
                ON worker_reward_ledger(
                    player_id
                )
                """
            )

        finally:
            db.close()

    def _now(
        self,
    ):
        return _validate_time(
            self.clock(),
            name="clock",
        )

    def _purge_expired(
        self,
        db,
        now,
    ):
        db.execute(
            """
            DELETE FROM worker_sessions
            WHERE lease_expires_at <= ?
            """,
            (
                now,
            ),
        )

        db.execute(
            """
            DELETE FROM used_worker_tickets
            WHERE expires_at <= ?
            """,
            (
                now,
            ),
        )

    def attach(
        self,
        *,
        worker_ticket,
        node_id,
        client_nonce,
    ):
        node_id = _validate_node_id(
            node_id
        )

        if not isinstance(
            client_nonce,
            str,
        ):
            raise TypeError(
                "client_nonce must be a string"
            )

        if (
            not client_nonce
            or len(client_nonce) > 256
        ):
            raise ValueError(
                "Invalid client_nonce"
            )

        now = self._now()

        try:
            ticket_payload = (
                verify_worker_ticket(
                    worker_ticket,
                    secret=(
                        self.ticket_secret
                    ),
                    now=now,
                )
            )
        except WorkerTicketError as exc:
            raise WorkerSessionAuthError(
                str(exc)
            ) from exc

        player_id = (
            ticket_payload["sub"]
        )

        ticket_id = (
            ticket_payload["jti"]
        )

        ticket_expires_at = float(
            ticket_payload["exp"]
        )

        session_id = (
            secrets.token_urlsafe(
                24
            )
        )

        session_token = (
            secrets.token_urlsafe(
                32
            )
        )

        token_digest = _token_digest(
            session_token
        )

        lease_expires_at = (
            now
            + self.lease_seconds
        )

        db = self._connect()

        try:
            db.execute(
                "BEGIN IMMEDIATE"
            )

            self._purge_expired(
                db,
                now,
            )

            used_ticket = db.execute(
                """
                SELECT ticket_id
                FROM used_worker_tickets
                WHERE ticket_id=?
                """,
                (
                    ticket_id,
                ),
            ).fetchone()

            if used_ticket:
                raise WorkerTicketUsedError(
                    "Worker Ticket "
                    "has already been used"
                )

            active_session = db.execute(
                """
                SELECT *
                FROM worker_sessions
                WHERE player_id=?
                  AND lease_expires_at>?
                """,
                (
                    player_id,
                    now,
                ),
            ).fetchone()

            if active_session:
                raise WorkerSessionActiveError(
                    "WORKER_SESSION_ACTIVE"
                )

            db.execute(
                """
                INSERT INTO used_worker_tickets(
                    ticket_id,
                    player_id,
                    used_at,
                    expires_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    ticket_id,
                    player_id,
                    now,
                    ticket_expires_at,
                ),
            )

            db.execute(
                """
                INSERT OR REPLACE
                INTO worker_sessions(
                    player_id,
                    session_id,
                    token_digest,
                    node_id,
                    lease_expires_at,
                    attached_at,
                    updated_at
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    player_id,
                    session_id,
                    token_digest,
                    node_id,
                    lease_expires_at,
                    now,
                    now,
                ),
            )

            db.execute(
                """
                INSERT OR IGNORE
                INTO worker_rewards(
                    player_id,
                    reward,
                    accepted_work_count,
                    updated_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    player_id,
                    0,
                    0,
                    now,
                ),
            )

            db.execute(
                "COMMIT"
            )

        except Exception:
            db.execute(
                "ROLLBACK"
            )
            raise

        finally:
            db.close()

        return {
            "worker_session_token":
                session_token,
            "worker_session_id":
                session_id,
            "worker_identity":
                player_id,
            "lease_seconds":
                self.lease_seconds,
            "lease_expires_at":
                lease_expires_at,
        }

    def authenticate(
        self,
        *,
        worker_session_token,
        node_id=None,
        renew=False,
    ):
        token_digest = _token_digest(
            worker_session_token
        )

        if node_id is not None:
            node_id = (
                _validate_node_id(
                    node_id
                )
            )

        if not isinstance(
            renew,
            bool,
        ):
            raise TypeError(
                "renew must be bool"
            )

        now = self._now()

        db = self._connect()

        try:
            if renew:
                db.execute(
                    "BEGIN IMMEDIATE"
                )

            row = db.execute(
                """
                SELECT *
                FROM worker_sessions
                WHERE token_digest=?
                  AND lease_expires_at>?
                """,
                (
                    token_digest,
                    now,
                ),
            ).fetchone()

            if not row:
                raise WorkerSessionAuthError(
                    "Invalid or expired "
                    "Worker session"
                )

            if (
                node_id is not None
                and not hmac.compare_digest(
                    row["node_id"],
                    node_id,
                )
            ):
                raise WorkerSessionAuthError(
                    "Worker node mismatch"
                )

            lease_expires_at = (
                row[
                    "lease_expires_at"
                ]
            )

            if renew:
                lease_expires_at = (
                    now
                    + self.lease_seconds
                )

                db.execute(
                    """
                    UPDATE worker_sessions
                    SET lease_expires_at=?,
                        updated_at=?
                    WHERE player_id=?
                      AND token_digest=?
                    """,
                    (
                        lease_expires_at,
                        now,
                        row["player_id"],
                        token_digest,
                    ),
                )

                db.execute(
                    "COMMIT"
                )

            return {
                "player_id":
                    row["player_id"],
                "worker_session_id":
                    row["session_id"],
                "node_id":
                    row["node_id"],
                "lease_expires_at":
                    lease_expires_at,
            }

        except Exception:
            if renew:
                try:
                    db.execute(
                        "ROLLBACK"
                    )
                except sqlite3.Error:
                    pass

            raise

        finally:
            db.close()

    def heartbeat(
        self,
        *,
        worker_session_token,
        node_id,
    ):
        return self.authenticate(
            worker_session_token=(
                worker_session_token
            ),
            node_id=node_id,
            renew=True,
        )

    def detach(
        self,
        *,
        worker_session_token,
        node_id=None,
    ):
        session = self.authenticate(
            worker_session_token=(
                worker_session_token
            ),
            node_id=node_id,
            renew=False,
        )

        token_digest = _token_digest(
            worker_session_token
        )

        db = self._connect()

        try:
            result = db.execute(
                """
                DELETE FROM worker_sessions
                WHERE player_id=?
                  AND token_digest=?
                """,
                (
                    session["player_id"],
                    token_digest,
                ),
            )

            detached = (
                result.rowcount == 1
            )

        finally:
            db.close()

        return {
            "detached": detached,
            "player_id":
                session["player_id"],
        }

    def credit_reward(
        self,
        *,
        worker_session_token,
        node_id,
        work_id,
        reward=1,
    ):
        work_id = _validate_work_id(
            work_id
        )

        if not isinstance(
            reward,
            int,
        ):
            raise TypeError(
                "reward must be an integer"
            )

        if (
            reward <= 0
            or reward > 1_000_000
        ):
            raise ValueError(
                "reward is outside "
                "the allowed range"
            )

        session = self.authenticate(
            worker_session_token=(
                worker_session_token
            ),
            node_id=node_id,
            renew=False,
        )

        player_id = (
            session["player_id"]
        )

        now = self._now()

        db = self._connect()

        credited = False

        try:
            db.execute(
                "BEGIN IMMEDIATE"
            )

            existing = db.execute(
                """
                SELECT work_id
                FROM worker_reward_ledger
                WHERE work_id=?
                """,
                (
                    work_id,
                ),
            ).fetchone()

            if existing is None:
                db.execute(
                    """
                    INSERT INTO
                    worker_reward_ledger(
                        work_id,
                        player_id,
                        node_id,
                        reward,
                        created_at
                    )
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        work_id,
                        player_id,
                        node_id,
                        reward,
                        now,
                    ),
                )

                db.execute(
                    """
                    INSERT INTO worker_rewards(
                        player_id,
                        reward,
                        accepted_work_count,
                        updated_at
                    )
                    VALUES(?,?,?,?)
                    ON CONFLICT(player_id)
                    DO UPDATE SET
                        reward=reward+excluded.reward,
                        accepted_work_count=
                            accepted_work_count+1,
                        updated_at=excluded.updated_at
                    """,
                    (
                        player_id,
                        reward,
                        1,
                        now,
                    ),
                )

                credited = True

            reward_row = db.execute(
                """
                SELECT *
                FROM worker_rewards
                WHERE player_id=?
                """,
                (
                    player_id,
                ),
            ).fetchone()

            db.execute(
                "COMMIT"
            )

        except Exception:
            db.execute(
                "ROLLBACK"
            )
            raise

        finally:
            db.close()

        return {
            "credited": credited,
            "player_id":
                player_id,
            "reward":
                reward_row["reward"],
            "accepted_work_count":
                reward_row[
                    "accepted_work_count"
                ],
        }

    def get_reward(
        self,
        *,
        worker_session_token,
        node_id=None,
    ):
        session = self.authenticate(
            worker_session_token=(
                worker_session_token
            ),
            node_id=node_id,
            renew=False,
        )

        db = self._connect()

        try:
            row = db.execute(
                """
                SELECT *
                FROM worker_rewards
                WHERE player_id=?
                """,
                (
                    session["player_id"],
                ),
            ).fetchone()

        finally:
            db.close()

        return {
            "player_id":
                session["player_id"],
            "reward":
                (
                    row["reward"]
                    if row
                    else 0
                ),
            "accepted_work_count":
                (
                    row[
                        "accepted_work_count"
                    ]
                    if row
                    else 0
                ),
        }
