# SPDX-License-Identifier: AGPL-3.0-only
"""Receiving Pickwise webhooks: a complete receiver in Python, standard library only.

What Pickwise sends
-------------------
A POST with a JSON body and these headers:

    X-Pickwise-Event:      the event type, e.g. "leave.request.approved"
    X-Pickwise-Delivery:   this delivery's id; the same on every retry of it
    X-Pickwise-Signature:  t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>

The body is one event:

    {"id": "<event id>", "type": "...", "created_at": "...", "tenant_id": "...",
     "aggregate": {"type": "...", "id": "..."}, "data": {...}}

What your receiver must do
--------------------------
1. Verify the signature over the RAW request bytes, before parsing the JSON. Re-serialising
   parsed JSON changes the bytes and breaks the check.
2. Reject timestamps more than five minutes from your clock, so a captured request can't be
   replayed later.
3. Compare in constant time (``hmac.compare_digest``).
4. Answer 2xx quickly (do the real work afterwards). Anything else, a timeout, or a redirect
   counts as a failure and is retried with growing delays for about two days.
5. Be idempotent. Delivery is at-least-once: a retry repeats the same X-Pickwise-Delivery, and
   a replay from the admin screen sends the same event again under a new delivery id. Key
   your side effects on the event ``id``.

The signing secret (``whsec_...``) is shown once, when you create the endpoint or rotate its
secret. Keep it in a secret store, not in code.

Run it:  PICKWISE_WEBHOOK_SECRET=whsec_... python receiver.py   (listens on 0.0.0.0:8089)
"""

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 1024 * 1024


def verify_signature(
    secret: str,
    body: bytes,
    header: str | None,
    *,
    now: float | None = None,
    tolerance_seconds: int = TOLERANCE_SECONDS,
) -> bool:
    """True if ``header`` is a valid, fresh signature of the raw ``body`` under ``secret``."""
    if not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        timestamp = int(parts["t"])
        received = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((time.time() if now is None else now) - timestamp) > tolerance_seconds:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received)


def make_handler(
    secret: str, on_event: Callable[[dict[str, Any]], None]
) -> type[BaseHTTPRequestHandler]:
    """A request handler class that verifies, de-duplicates and then calls ``on_event(event)``.

    The in-memory ``seen`` set is only for the example. In production, record processed event
    ids in your database, in the same transaction as the side effect.
    """
    seen: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            if not verify_signature(secret, body, self.headers.get("X-Pickwise-Signature")):
                self.send_error(401, "bad signature")
                return
            event = json.loads(body)
            if event["id"] not in seen:
                seen.add(event["id"])
                on_event(event)
            self.send_response(204)  # acknowledge fast; heavy work belongs in a queue
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass  # keep request bodies and secrets out of logs

    return Handler


if __name__ == "__main__":
    secret = os.environ["PICKWISE_WEBHOOK_SECRET"]

    def show(event: dict[str, Any]) -> None:
        print(f"{event['type']} {event['id']}")

    HTTPServer(("0.0.0.0", 8089), make_handler(secret, show)).serve_forever()  # noqa: S104
