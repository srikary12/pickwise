# SPDX-License-Identifier: AGPL-3.0-only
"""The example receivers verify what Pickwise's real signer produces.

``examples/webhook-receiver/vector.json`` is a request signed by ``webhooks.signing``: this
test regenerates it from the real signer (so it can't drift) and runs the Python example's
verifier and HTTP server against it. The Node example runs the same vector in its own tests
(``make test-examples``).
"""

import datetime
import importlib.util
import json
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from http.server import HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pickwise.platform.events import OutboxEvent
from pickwise.platform.webhooks.delivery import build_body
from pickwise.platform.webhooks.signing import sign

EXAMPLES = Path(__file__).resolve().parents[4] / "examples" / "webhook-receiver"
VECTOR = json.loads((EXAMPLES / "vector.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def receiver() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "example_receiver", EXAMPLES / "python" / "receiver.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_vector_is_what_the_real_signer_produces() -> None:
    body = VECTOR["body"].encode()
    assert sign(VECTOR["secret"], body, VECTOR["timestamp"]) == VECTOR["signature"]
    # And the body is the format build_body emits: compact, sorted keys.
    document = json.loads(body)
    event = OutboxEvent(
        id=uuid.UUID(document["id"]),
        tenant_id=uuid.UUID(document["tenant_id"]),
        aggregate_type=document["aggregate"]["type"],
        aggregate_id=uuid.UUID(document["aggregate"]["id"]),
        event_type=document["type"],
        payload=document["data"],
        occurred_at=datetime.datetime.fromisoformat(document["created_at"]),
    )
    assert build_body(event) == body


def test_the_python_example_accepts_the_vector_and_refuses_tampering(receiver: ModuleType) -> None:
    body = VECTOR["body"].encode()
    now = VECTOR["timestamp"] + 5
    verify = receiver.verify_signature
    assert verify(VECTOR["secret"], body, VECTOR["signature"], now=now)
    assert not verify("whsec_other", body, VECTOR["signature"], now=now)
    assert not verify(VECTOR["secret"], body + b" ", VECTOR["signature"], now=now)
    assert not verify(VECTOR["secret"], body, VECTOR["signature"], now=VECTOR["timestamp"] + 301)
    for malformed in (None, "", "v1=abc", "t=abc,v1=abc", "garbage"):
        assert not verify(VECTOR["secret"], body, malformed, now=now)


@pytest.fixture
def server(receiver: ModuleType) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    events: list[dict[str, Any]] = []
    httpd = HTTPServer(("127.0.0.1", 0), receiver.make_handler(VECTOR["secret"], events.append))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/", events
    httpd.shutdown()
    thread.join()


def post(url: str, body: bytes, signature: str) -> int:
    request = urllib.request.Request(  # noqa: S310 - a loopback URL we just built
        url, data=body, headers={"X-Pickwise-Signature": signature}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


def test_the_python_server_verifies_acknowledges_and_deduplicates(
    server: tuple[str, list[dict[str, Any]]],
) -> None:
    import time

    url, events = server
    payload = json.dumps({"id": "evt_1", "type": "leave.request.approved"}).encode()
    now = int(time.time())
    assert post(url, payload, sign(VECTOR["secret"], payload, now)) == 204
    assert post(url, payload, sign(VECTOR["secret"], payload, now)) == 204  # a retry or replay
    assert len(events) == 1
    assert post(url, payload, sign("whsec_forged", payload, now)) == 401
    assert post(url, payload, sign(VECTOR["secret"], payload, now - 3600)) == 401  # stale
    assert len(events) == 1
