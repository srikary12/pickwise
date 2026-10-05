# SPDX-License-Identifier: AGPL-3.0-only
"""Webhook endpoints and the delivery log."""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.crypto import Keyring
from pickwise.platform.events.relay import MAX_ATTEMPTS as EVENT_MAX_ATTEMPTS
from pickwise.platform.jobs import JobQueueUnavailableError, defer
from pickwise.platform.webhooks.delivery import secret_aad
from pickwise.platform.webhooks.ssrf import Resolver, UnsafeTargetError, resolve_target
from pickwise.shared.errors import ConflictError, NotFoundError, UnprocessableError
from pickwise.shared.ids import uuid7
from pickwise.shared.logging import get_logger

log = get_logger(__name__)

MAX_ENDPOINTS = 20
_COLUMNS = "id, url, event_types, is_active, created_at, row_version"
_DELIVERY_COLUMNS = (
    "d.id, d.endpoint_id, d.event_id, e.event_type, d.status, d.attempt, d.response_code, "
    "d.next_attempt_at, d.last_error, d.created_at"
)


@dataclass(frozen=True, slots=True)
class Endpoint:
    id: uuid.UUID
    url: str
    event_types: list[str]
    is_active: bool
    created_at: datetime
    row_version: int


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


async def check_url(url: str, *, allow_private: bool, resolver: Resolver) -> None:
    try:
        await resolve_target(url, allow_private=allow_private, resolver=resolver)
    except UnsafeTargetError as exc:
        raise UnprocessableError(str(exc), code="unsafe_webhook_target") from exc


async def create_endpoint(
    db: AsyncSession,
    keyring: Keyring,
    tenant_id: uuid.UUID,
    *,
    url: str,
    event_types: list[str],
    allow_private: bool,
    resolver: Resolver,
) -> tuple[Endpoint, str]:
    await check_url(url, allow_private=allow_private, resolver=resolver)
    count: int = (
        await db.execute(text("SELECT count(*) FROM platform.webhook_endpoints"))
    ).scalar_one()
    if count >= MAX_ENDPOINTS:
        raise UnprocessableError(
            f"A tenant can have at most {MAX_ENDPOINTS} webhook endpoints.",
            code="too_many_endpoints",
        )
    endpoint_id = uuid7()
    secret = new_secret()
    row = (
        await db.execute(
            text(
                "INSERT INTO platform.webhook_endpoints (id, url, secret_enc, event_types) "  # noqa: S608
                f"VALUES (:id, :url, :enc, :types) RETURNING {_COLUMNS}"
            ),
            {
                "id": endpoint_id,
                "url": url,
                "enc": keyring.encrypt(secret.encode(), secret_aad(tenant_id, endpoint_id)),
                "types": event_types,
            },
        )
    ).one()
    return Endpoint(*row), secret


async def list_endpoints(db: AsyncSession) -> list[Endpoint]:
    rows = (
        await db.execute(text(f"SELECT {_COLUMNS} FROM platform.webhook_endpoints ORDER BY id"))  # noqa: S608
    ).all()
    return [Endpoint(*r) for r in rows]


async def get_endpoint(db: AsyncSession, endpoint_id: uuid.UUID) -> Endpoint:
    row = (
        await db.execute(
            text(f"SELECT {_COLUMNS} FROM platform.webhook_endpoints WHERE id = :id"),  # noqa: S608
            {"id": endpoint_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Webhook endpoint not found.")
    return Endpoint(*row)


async def update_endpoint(
    db: AsyncSession,
    endpoint_id: uuid.UUID,
    *,
    url: str,
    event_types: list[str],
    is_active: bool,
    row_version: int,
    allow_private: bool,
    resolver: Resolver,
) -> Endpoint:
    current = await get_endpoint(db, endpoint_id)
    if url != current.url:
        await check_url(url, allow_private=allow_private, resolver=resolver)
    row = (
        await db.execute(
            text(
                "UPDATE platform.webhook_endpoints SET url = :url, event_types = :types, "  # noqa: S608
                "is_active = :active WHERE id = :id AND row_version = :v "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "url": url,
                "types": event_types,
                "active": is_active,
                "id": endpoint_id,
                "v": row_version,
            },
        )
    ).one_or_none()
    if row is None:
        raise ConflictError(
            "Someone else changed this endpoint. Reload and try again.", code="stale_row_version"
        )
    return Endpoint(*row)


async def rotate_secret(
    db: AsyncSession, keyring: Keyring, tenant_id: uuid.UUID, endpoint_id: uuid.UUID
) -> str:
    await get_endpoint(db, endpoint_id)
    secret = new_secret()
    await db.execute(
        text("UPDATE platform.webhook_endpoints SET secret_enc = :enc WHERE id = :id"),
        {
            "enc": keyring.encrypt(secret.encode(), secret_aad(tenant_id, endpoint_id)),
            "id": endpoint_id,
        },
    )
    return secret


async def list_deliveries(
    db: AsyncSession,
    *,
    endpoint_id: uuid.UUID | None,
    status: str | None,
    event_type: str | None,
    before: uuid.UUID | None,
    limit: int,
) -> tuple[list[dict[str, Any]], uuid.UUID | None]:
    rows = (
        (
            await db.execute(
                text(
                    f"SELECT {_DELIVERY_COLUMNS} FROM platform.webhook_deliveries d "  # noqa: S608
                    "JOIN platform.outbox_events e "
                    "  ON e.tenant_id = d.tenant_id AND e.id = d.event_id "
                    "WHERE (CAST(:endpoint AS uuid) IS NULL OR d.endpoint_id = :endpoint) "
                    "  AND (CAST(:status AS text) IS NULL OR d.status = :status) "
                    "  AND (CAST(:etype AS text) IS NULL OR e.event_type = :etype) "
                    "  AND (CAST(:before AS uuid) IS NULL OR d.id < :before) "
                    "ORDER BY d.id DESC LIMIT :n"
                ),
                {
                    "endpoint": endpoint_id,
                    "status": status,
                    "etype": event_type,
                    "before": before,
                    "n": limit + 1,
                },
            )
        )
        .mappings()
        .all()
    )
    items = [dict(r) for r in rows]
    more = len(items) > limit
    return items[:limit], items[limit - 1]["id"] if more else None


async def get_delivery(db: AsyncSession, delivery_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                text(
                    f"SELECT {_DELIVERY_COLUMNS} FROM platform.webhook_deliveries d "  # noqa: S608
                    "JOIN platform.outbox_events e "
                    "  ON e.tenant_id = d.tenant_id AND e.id = d.event_id "
                    "WHERE d.id = :id"
                ),
                {"id": delivery_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFoundError("Delivery not found.")
    return dict(row)


async def replay_delivery(db: AsyncSession, delivery_id: uuid.UUID) -> uuid.UUID:
    """Queue a fresh delivery of the same event to the same endpoint. The original row stays
    as history. The endpoint's current secret signs it."""
    original = await get_delivery(db, delivery_id)
    if original["status"] == "pending":
        raise ConflictError("That delivery is still being attempted.", code="delivery_pending")
    new_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO platform.webhook_deliveries (endpoint_id, event_id, next_attempt_at) "
                "VALUES (:endpoint, :event, now()) RETURNING id"
            ),
            {"endpoint": original["endpoint_id"], "event": original["event_id"]},
        )
    ).scalar_one()
    return new_id


async def retry_event(db: AsyncSession, event_id: uuid.UUID) -> None:
    """Let the relay try an event again after it gave up on a failing in-process handler."""
    result = await db.execute(
        text(
            "UPDATE platform.outbox_events SET attempts = 0 "
            "WHERE id = :id AND published_at IS NULL AND attempts >= :max"
        ),
        {"id": event_id, "max": EVENT_MAX_ATTEMPTS},
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise NotFoundError("No stuck event with that id.")


async def kick_delivery(tenant_id: uuid.UUID, delivery_id: uuid.UUID) -> None:
    """Ask the worker to deliver now instead of at the next sweep. Best effort, after commit."""
    try:
        await defer(
            "pickwise.deliver_webhook", delivery_id=str(delivery_id), tenant_id=str(tenant_id)
        )
    except JobQueueUnavailableError:
        pass
    except Exception as exc:  # noqa: BLE001 - the sweep delivers it; never fail the request
        log.warning("delivery kick failed; the sweep will deliver", error=type(exc).__name__)


async def send_test_event(db: AsyncSession, endpoint_id: uuid.UUID) -> uuid.UUID:
    """Queue a ``webhook.ping`` to this endpoint only, so a receiver can be checked end to
    end. The event is created already published: the relay must not fan it out to others."""
    endpoint = await get_endpoint(db, endpoint_id)
    if not endpoint.is_active:
        raise ConflictError("Enable the endpoint first.", code="endpoint_disabled")
    event_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO platform.outbox_events "
                "(aggregate_type, aggregate_id, event_type, payload, published_at) "
                "VALUES ('webhook_endpoint', :e, 'webhook.ping', '{}', now()) RETURNING id"
            ),
            {"e": endpoint_id},
        )
    ).scalar_one()
    delivery_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO platform.webhook_deliveries (endpoint_id, event_id, next_attempt_at) "
                "VALUES (:endpoint, :event, now()) RETURNING id"
            ),
            {"endpoint": endpoint_id, "event": event_id},
        )
    ).scalar_one()
    return delivery_id
