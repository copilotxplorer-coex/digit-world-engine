"""
Short-lived signed tickets for attaching a CLI worker.

The Login Key and game session token are never included in
the ticket. The ticket contains only a digest of the current
game session and is signed with HMAC-SHA256.
"""

import base64
import hashlib
import hmac
import json
import math
import secrets
import time


VERSION = 1

DEFAULT_TTL_SECONDS = 60

MIN_TTL_SECONDS = 10

MAX_TTL_SECONDS = 300


class WorkerTicketError(ValueError):
    """Raised when a worker ticket is invalid or expired."""


def _b64url_encode(raw):
    return (
        base64.urlsafe_b64encode(
            raw
        )
        .rstrip(b"=")
        .decode("ascii")
    )


def _b64url_decode(value):
    if not isinstance(value, str):
        raise WorkerTicketError(
            "Invalid worker ticket encoding"
        )

    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise WorkerTicketError(
            "Invalid worker ticket encoding"
        ) from exc

    padding = b"=" * (
        (-len(raw)) % 4
    )

    try:
        return base64.urlsafe_b64decode(
            raw + padding
        )
    except Exception as exc:
        raise WorkerTicketError(
            "Invalid worker ticket encoding"
        ) from exc


def _require_secret(secret):
    if isinstance(secret, str):
        secret = secret.encode(
            "utf-8"
        )

    if not isinstance(
        secret,
        bytes,
    ):
        raise TypeError(
            "Worker ticket secret must be bytes or string"
        )

    if len(secret) < 32:
        raise ValueError(
            "Worker ticket secret must contain at least 32 bytes"
        )

    return secret


def _require_identity(player_id):
    if not isinstance(
        player_id,
        str,
    ):
        raise TypeError(
            "player_id must be a string"
        )

    player_id = player_id.strip()

    if not player_id:
        raise ValueError(
            "player_id is required"
        )

    if len(player_id) > 128:
        raise ValueError(
            "player_id is too long"
        )

    return player_id


def _session_digest(session_token):
    if not isinstance(
        session_token,
        str,
    ):
        raise TypeError(
            "session_token must be a string"
        )

    if not session_token:
        raise ValueError(
            "session_token is required"
        )

    if len(session_token) > 512:
        raise ValueError(
            "session_token is too long"
        )

    return hashlib.sha256(
        session_token.encode(
            "utf-8"
        )
    ).hexdigest()


def issue_worker_ticket(
    *,
    player_id,
    session_token,
    secret,
    ttl_seconds=DEFAULT_TTL_SECONDS,
    now=None,
):
    """
    Return a short-lived HMAC signed worker ticket.

    The plaintext Login Key and plaintext game session token
    are not stored in the ticket.
    """

    secret = _require_secret(
        secret
    )

    player_id = _require_identity(
        player_id
    )

    if not isinstance(
        ttl_seconds,
        int,
    ):
        raise TypeError(
            "ttl_seconds must be an integer"
        )

    if not (
        MIN_TTL_SECONDS
        <= ttl_seconds
        <= MAX_TTL_SECONDS
    ):
        raise ValueError(
            "ttl_seconds is outside the allowed range"
        )

    if now is None:
        now = time.time()

    if not isinstance(
        now,
        (int, float),
    ):
        raise TypeError(
            "now must be numeric"
        )

    if not math.isfinite(now):
        raise ValueError(
            "now must be finite"
        )

    issued_at = int(now)

    payload = {
        "v": VERSION,
        "jti": secrets.token_urlsafe(
            24
        ),
        "sub": player_id,
        "sid": _session_digest(
            session_token
        ),
        "iat": issued_at,
        "exp": (
            issued_at
            + ttl_seconds
        ),
        "purpose": "worker-attach",
    }

    payload_raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        allow_nan=False,
    ).encode(
        "utf-8"
    )

    payload_part = _b64url_encode(
        payload_raw
    )

    signature = hmac.new(
        secret,
        payload_part.encode(
            "ascii"
        ),
        hashlib.sha256,
    ).digest()

    signature_part = _b64url_encode(
        signature
    )

    return (
        payload_part
        + "."
        + signature_part
    )


def verify_worker_ticket(
    ticket,
    *,
    secret,
    now=None,
    clock_skew_seconds=5,
):
    """
    Verify signature, structure, purpose and expiration.

    Returns the verified payload. One-time-use and active-worker
    enforcement are intentionally handled by the persistent
    worker-session store in a later layer.
    """

    secret = _require_secret(
        secret
    )

    if not isinstance(
        ticket,
        str,
    ):
        raise WorkerTicketError(
            "Worker ticket must be a string"
        )

    if not ticket or len(ticket) > 4096:
        raise WorkerTicketError(
            "Invalid worker ticket"
        )

    parts = ticket.split(".")

    if len(parts) != 2:
        raise WorkerTicketError(
            "Invalid worker ticket"
        )

    payload_part = parts[0]

    signature_part = parts[1]

    expected_signature = hmac.new(
        secret,
        payload_part.encode(
            "ascii"
        ),
        hashlib.sha256,
    ).digest()

    supplied_signature = _b64url_decode(
        signature_part
    )

    if not hmac.compare_digest(
        expected_signature,
        supplied_signature,
    ):
        raise WorkerTicketError(
            "Invalid worker ticket signature"
        )

    payload_raw = _b64url_decode(
        payload_part
    )

    try:
        payload = json.loads(
            payload_raw.decode(
                "utf-8"
            )
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise WorkerTicketError(
            "Invalid worker ticket payload"
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise WorkerTicketError(
            "Invalid worker ticket payload"
        )

    required = {
        "v",
        "jti",
        "sub",
        "sid",
        "iat",
        "exp",
        "purpose",
    }

    if set(payload) != required:
        raise WorkerTicketError(
            "Invalid worker ticket fields"
        )

    if payload["v"] != VERSION:
        raise WorkerTicketError(
            "Unsupported worker ticket version"
        )

    if (
        payload["purpose"]
        != "worker-attach"
    ):
        raise WorkerTicketError(
            "Invalid worker ticket purpose"
        )

    _require_identity(
        payload["sub"]
    )

    if (
        not isinstance(
            payload["jti"],
            str,
        )
        or not payload["jti"]
        or len(payload["jti"]) > 128
    ):
        raise WorkerTicketError(
            "Invalid worker ticket id"
        )

    if (
        not isinstance(
            payload["sid"],
            str,
        )
        or len(payload["sid"]) != 64
    ):
        raise WorkerTicketError(
            "Invalid worker session digest"
        )

    try:
        int(
            payload["sid"],
            16,
        )
    except ValueError as exc:
        raise WorkerTicketError(
            "Invalid worker session digest"
        ) from exc

    if not isinstance(
        payload["iat"],
        int,
    ):
        raise WorkerTicketError(
            "Invalid worker ticket issue time"
        )

    if not isinstance(
        payload["exp"],
        int,
    ):
        raise WorkerTicketError(
            "Invalid worker ticket expiration"
        )

    if (
        payload["exp"]
        <= payload["iat"]
    ):
        raise WorkerTicketError(
            "Invalid worker ticket lifetime"
        )

    if (
        payload["exp"]
        - payload["iat"]
        > MAX_TTL_SECONDS
    ):
        raise WorkerTicketError(
            "Worker ticket lifetime is too long"
        )

    if not isinstance(
        clock_skew_seconds,
        (int, float),
    ):
        raise TypeError(
            "clock_skew_seconds must be numeric"
        )

    if (
        clock_skew_seconds < 0
        or clock_skew_seconds > 60
    ):
        raise ValueError(
            "clock_skew_seconds is outside the allowed range"
        )

    if now is None:
        now = time.time()

    if not isinstance(
        now,
        (int, float),
    ):
        raise TypeError(
            "now must be numeric"
        )

    if not math.isfinite(now):
        raise ValueError(
            "now must be finite"
        )

    if (
        payload["iat"]
        > now + clock_skew_seconds
    ):
        raise WorkerTicketError(
            "Worker ticket is not active yet"
        )

    if (
        payload["exp"]
        <= now - clock_skew_seconds
    ):
        raise WorkerTicketError(
            "Worker ticket expired"
        )

    return payload
