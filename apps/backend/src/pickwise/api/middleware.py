# SPDX-License-Identifier: AGPL-3.0-only
"""Request-id and access-log middleware (pure ASGI, so it works for streaming responses)."""

import ipaddress
import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pickwise.shared.logging import get_logger

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller-supplied id only if it's short and boring; otherwise mint one.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

log = get_logger("pickwise.access")

# Container healthchecks hit these constantly; log them only when they fail.
PROBE_PATHS = frozenset({"/healthz", "/readyz"})


def _incoming_request_id(scope: Scope) -> str | None:
    wanted = REQUEST_ID_HEADER.lower().encode()
    for name, value in scope.get("headers", []):
        if name == wanted:
            candidate = value.decode("latin-1")
            return candidate if _SAFE_REQUEST_ID.match(candidate) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            # Path only, never the query string: it can carry tokens or PII.
            quiet = scope["path"] in PROBE_PATHS and status_code < 400
            (log.debug if quiet else log.info)(
                "request",
                method=scope["method"],
                path=scope["path"],
                status=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            structlog.contextvars.clear_contextvars()


class ClientIpMiddleware:
    """Resolve the client address, trusting X-Forwarded-For only from our proxies.

    The reverse proxy (Next.js in dev, Caddy in prod) appends the address it saw;
    we walk the header from the right and stop at the first untrusted hop.
    """

    def __init__(self, app: ASGIApp, trusted: list[IPNetwork]) -> None:
        self.app = app
        self.trusted = trusted

    def _is_trusted(self, address: str) -> bool:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in net for net in self.trusted)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            peer = scope["client"][0] if scope.get("client") else None
            client_ip = peer
            if peer and self._is_trusted(peer):
                forwarded = next(
                    (
                        v.decode("latin-1")
                        for k, v in scope.get("headers", [])
                        if k == b"x-forwarded-for"
                    ),
                    "",
                )
                for hop in reversed([h.strip() for h in forwarded.split(",") if h.strip()]):
                    client_ip = hop
                    if not self._is_trusted(hop):
                        break
            try:
                client_ip = str(ipaddress.ip_address(client_ip)) if client_ip else None
            except ValueError:
                client_ip = None
            scope.setdefault("state", {})["client_ip"] = client_ip
        await self.app(scope, receive, send)


_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
_API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
_HSTS = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")


class SecurityHeadersMiddleware:
    """Baseline response headers. The JSON API needs no CSP beyond 'none'; the dev
    docs page (/docs) loads its UI from a CDN, so it's left alone."""

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope["path"].startswith(("/docs", "/openapi.json"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS:
                    headers.setdefault(name.decode(), value.decode())
                if not is_docs:
                    headers.setdefault(_API_CSP[0].decode(), _API_CSP[1].decode())
                if self.hsts:
                    headers.setdefault(_HSTS[0].decode(), _HSTS[1].decode())
            await send(message)

        await self.app(scope, receive, send_with_headers)
