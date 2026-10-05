# SPDX-License-Identifier: AGPL-3.0-only
"""Webhook signatures.

``X-Pickwise-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>``

The timestamp is inside the signed string, so a captured delivery can't be replayed
later: receivers reject timestamps outside a tolerance window (five minutes is typical).
``examples/webhook-receiver`` holds standalone verifiers in Python and Node; a test
runs them against this signer.
"""

import hashlib
import hmac
import time

DEFAULT_TOLERANCE_SECONDS = 300


def sign(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify(
    secret: str,
    body: bytes,
    header: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Check a signature header. Constant-time; False on any malformed input."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        timestamp = int(parts["t"])
        signature = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - timestamp) > tolerance_seconds:
        return False
    expected = sign(secret, body, timestamp).split("v1=", 1)[1]
    return hmac.compare_digest(expected, signature)
