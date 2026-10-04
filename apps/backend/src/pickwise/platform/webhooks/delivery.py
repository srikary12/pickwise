# SPDX-License-Identifier: AGPL-3.0-only
"""Delivering one webhook: lease, send, record.

The HTTP call happens between two short transactions, never inside one that holds row
locks. Leasing pushes ``next_attempt_at`` out and counts the attempt, so a worker that
dies mid-send makes the delivery come back later instead of being lost or retried in a
tight loop. A delivery that exhausts ``MAX_ATTEMPTS`` is dead-lettered and can be replayed
from the API.
"""

import datetime
import json
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.crypto import KeyEncryptionKey, load_tenant_keyring
from pickwise.platform.events.registry import OutboxEvent
from pickwise.platform.webhooks.signing import sign
from pickwise.platform.webhooks.ssrf import (
    Resolver,
    UnsafeTargetError,
    resolve_target,
    system_resolver,
)
from pickwise.shared.db import Database, ops_task
from pickwise.shared.logging import get_logger
from pickwise.shared.settings import Settings

log = get_logger(__name__)

MAX_ATTEMPTS = 8
# Wait after the Nth failed attempt before the next one.
BACKOFF = tuple(datetime.timedelta(seconds=s) for s in (30, 120, 600, 1800, 7200, 21600, 86400))
LEASE = datetime.timedelta(minutes=2)
SWEEP_BATCH = 100
USER_AGENT = "Pickwise-Webhooks/1"


def secret_aad(tenant_id: uuid.UUID, endpoint_id: uuid.UUID) -> bytes:
    return f"platform.webhook_endpoints.secret_enc:{tenant_id}:{endpoint_id}".encode()


def build_body(event: OutboxEvent) -> bytes:
    """The JSON a receiver gets. Compact and key-sorted so the bytes are stable."""
    document = {
        "id": str(event.id),
        "type": event.event_type,
        "created_at": event.occurred_at.astimezone(datetime.UTC).isoformat(),
        "tenant_id": str(event.tenant_id),
        "aggregate": {"type": event.aggregate_type, "id": str(event.aggregate_id)},
        "data": event.payload,
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()


@dataclass(frozen=True, slots=True)
class Lease:
    tenant_id: uuid.UUID
    delivery_id: uuid.UUID
    attempt: int
    url: str
    secret: str
    event: OutboxEvent


@dataclass(frozen=True, slots=True)
class Attempt:
    ok: bool
    response_code: int | None = None
    error: str | None = None
    # A policy refusal (blocked address): retrying can't help until someone fixes the endpoint.
    permanent: bool = False


async def due_deliveries(
    session: AsyncSession, *, now: datetime.datetime, limit: int = SWEEP_BATCH
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(tenant_id, delivery_id) of pending deliveries whose time has come. Ops session."""
    rows = (
        await session.execute(
            text(
                "SELECT tenant_id, id FROM platform.webhook_deliveries "
                "WHERE status = 'pending' AND next_attempt_at <= :now "
                "ORDER BY next_attempt_at LIMIT :n"
            ),
            {"now": now, "n": limit},
        )
    ).all()
    return [(r.tenant_id, r.id) for r in rows]


async def _lease(
    session: AsyncSession,
    kek: KeyEncryptionKey,
    tenant_id: uuid.UUID,
    delivery_id: uuid.UUID,
    now: datetime.datetime,
) -> Lease | None:
    row = (
        await session.execute(
            text(
                "SELECT d.attempt, w.id AS endpoint_id, w.url, w.secret_enc, w.is_active, "
                "       e.id AS event_id, e.aggregate_type, e.aggregate_id, e.event_type, "
                "       e.payload, e.occurred_at "
                "FROM platform.webhook_deliveries d "
                "JOIN platform.webhook_endpoints w "
                "  ON w.tenant_id = d.tenant_id AND w.id = d.endpoint_id "
                "JOIN platform.outbox_events e "
                "  ON e.tenant_id = d.tenant_id AND e.id = d.event_id "
                "WHERE d.tenant_id = :t AND d.id = :id AND d.status = 'pending' "
                "  AND d.next_attempt_at <= :now "
                "FOR UPDATE OF d SKIP LOCKED"
            ),
            {"t": tenant_id, "id": delivery_id, "now": now},
        )
    ).one_or_none()
    if row is None:
        return None
    if not row.is_active:
        await session.execute(
            text(
                "UPDATE platform.webhook_deliveries SET status = 'dead', next_attempt_at = NULL, "
                "last_error = 'endpoint is disabled' WHERE tenant_id = :t AND id = :id"
            ),
            {"t": tenant_id, "id": delivery_id},
        )
        return None
    await session.execute(
        text(
            "UPDATE platform.webhook_deliveries SET attempt = attempt + 1, "
            "next_attempt_at = :lease "
            "WHERE tenant_id = :t AND id = :id"
        ),
        {"t": tenant_id, "id": delivery_id, "lease": now + LEASE},
    )
    keyring = await load_tenant_keyring(session, kek, tenant_id)
    secret = keyring.decrypt(bytes(row.secret_enc), secret_aad(tenant_id, row.endpoint_id)).decode()
    return Lease(
        tenant_id,
        delivery_id,
        row.attempt + 1,
        row.url,
        secret,
        OutboxEvent(
            row.event_id,
            tenant_id,
            row.aggregate_type,
            row.aggregate_id,
            row.event_type,
            row.payload,
            row.occurred_at,
        ),
    )


async def send(
    client: httpx.AsyncClient,
    settings: Settings,
    lease: Lease,
    *,
    resolver: Resolver = system_resolver,
    now: datetime.datetime,
) -> Attempt:
    """One signed POST to the leased endpoint. Never raises: the outcome is the result."""
    allow_private = settings.webhook_allow_private_targets
    try:
        target = await resolve_target(lease.url, allow_private=allow_private, resolver=resolver)
    except UnsafeTargetError as exc:
        return Attempt(False, error=f"blocked: {exc}", permanent=True)
    body = build_body(lease.event)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Host": target.host if target.port in (80, 443) else f"{target.host}:{target.port}",
        "X-Pickwise-Event": lease.event.event_type,
        "X-Pickwise-Delivery": str(lease.delivery_id),
        "X-Pickwise-Signature": sign(lease.secret, body, int(now.timestamp())),
    }
    try:
        # Connect to the address we checked; TLS still verifies the certificate against
        # the real host name (SNI).
        async with client.stream(
            "POST",
            target.pinned_url(),
            content=body,
            headers=headers,
            extensions={"sni_hostname": target.host},
            timeout=settings.webhook_timeout_seconds,
        ) as response:
            code = response.status_code
    except httpx.TimeoutException:
        return Attempt(False, error="timeout")
    except httpx.HTTPError as exc:
        return Attempt(False, error=f"connection failed ({type(exc).__name__})")
    if 200 <= code < 300:
        return Attempt(True, response_code=code)
    if 300 <= code < 400:
        return Attempt(False, response_code=code, error="redirect refused")
    return Attempt(False, response_code=code, error=f"HTTP {code}")


async def _record(
    session: AsyncSession, lease: Lease, attempt: Attempt, now: datetime.datetime
) -> str:
    if attempt.ok:
        status, next_at = "succeeded", None
    elif attempt.permanent or lease.attempt >= MAX_ATTEMPTS:
        status, next_at = "dead", None
    else:
        status = "pending"
        next_at = now + BACKOFF[min(lease.attempt, len(BACKOFF)) - 1]
    await session.execute(
        text(
            "UPDATE platform.webhook_deliveries SET status = :s, next_attempt_at = :next, "
            "response_code = :code, last_error = :err WHERE tenant_id = :t AND id = :id"
        ),
        {
            "s": status,
            "next": next_at,
            "code": attempt.response_code,
            "err": attempt.error,
            "t": lease.tenant_id,
            "id": lease.delivery_id,
        },
    )
    return status


@ops_task
async def deliver_one(
    database: Database,
    settings: Settings,
    kek: KeyEncryptionKey,
    client: httpx.AsyncClient,
    tenant_id: uuid.UUID,
    delivery_id: uuid.UUID,
    *,
    resolver: Resolver = system_resolver,
    now: datetime.datetime | None = None,
) -> str:
    """Attempt one delivery if it is due. Returns its resulting status, or "skipped"."""
    now = now or datetime.datetime.now(datetime.UTC)
    async with database.ops_session() as session:
        lease = await _lease(session, kek, tenant_id, delivery_id, now)
    if lease is None:
        return "skipped"
    attempt = await send(client, settings, lease, resolver=resolver, now=now)
    async with database.ops_session() as session:
        status = await _record(session, lease, attempt, now)
    log.info(
        "webhook attempted",
        delivery_id=str(delivery_id),
        attempt=lease.attempt,
        status=status,
        response_code=attempt.response_code,
    )
    return status
