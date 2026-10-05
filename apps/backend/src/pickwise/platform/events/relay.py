# SPDX-License-Identifier: AGPL-3.0-only
"""The relay: publishes committed outbox events.

Runs in the worker as an ops task (the outbox is read across tenants) and does two
things per event: runs the registered in-process handlers, each in a tenant session so
RLS applies to them, then queues one webhook delivery per matching endpoint. An event is
marked published in the same transaction that queues its deliveries.

Handlers run at-least-once. If one raises, the event stays unpublished, its attempt
counter goes up, and the next run retries it; after ``MAX_ATTEMPTS`` it is left for an
operator and can be retried through the webhooks API. Rows are claimed with
``FOR UPDATE SKIP LOCKED``, so concurrent relays never take the same event.
"""

import datetime
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.events.registry import EVENT_HANDLERS, OutboxEvent
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database
from pickwise.shared.logging import get_logger

log = get_logger(__name__)

MAX_ATTEMPTS = 10
BATCH_SIZE = 50

_CLAIM = text(
    "SELECT id, tenant_id, aggregate_type, aggregate_id, event_type, payload, occurred_at "
    "FROM platform.outbox_events "
    "WHERE published_at IS NULL AND attempts < :max "
    "ORDER BY occurred_at, id LIMIT :n FOR UPDATE SKIP LOCKED"
)
# An endpoint subscribes to exact types and "prefix.*" patterns; no types means everything.
_FAN_OUT = text(
    "INSERT INTO platform.webhook_deliveries (tenant_id, endpoint_id, event_id, next_attempt_at) "
    "SELECT w.tenant_id, w.id, :event_id, :now FROM platform.webhook_endpoints w "
    "WHERE w.tenant_id = :tenant_id AND w.is_active "
    "  AND (cardinality(w.event_types) = 0 OR EXISTS ("
    "        SELECT 1 FROM unnest(w.event_types) s WHERE s = :type "
    "           OR (s LIKE '%.*' AND starts_with(:type, left(s, -1))))) "
    "RETURNING id"
)


@dataclass(slots=True)
class RelayResult:
    published: int = 0
    failed: int = 0
    # (tenant_id, delivery_id) pairs to defer after the commit.
    deliveries: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)


async def relay_batch(
    session: AsyncSession,
    database: Database,
    *,
    now: datetime.datetime | None = None,
    limit: int = BATCH_SIZE,
) -> RelayResult:
    """Publish up to ``limit`` events. ``session`` is an ops session."""
    now = now or datetime.datetime.now(datetime.UTC)
    result = RelayResult()
    rows = (await session.execute(_CLAIM, {"max": MAX_ATTEMPTS, "n": limit})).all()
    for row in rows:
        event = OutboxEvent(*row)
        failed_handler = await _run_handlers(database, event)
        if failed_handler is not None:
            attempts: int = (
                await session.execute(
                    text(
                        "UPDATE platform.outbox_events SET attempts = attempts + 1 "
                        "WHERE tenant_id = :t AND id = :id RETURNING attempts"
                    ),
                    {"t": event.tenant_id, "id": event.id},
                )
            ).scalar_one()
            result.failed += 1
            log.error(
                "event handler failed",
                event_id=str(event.id),
                event_type=event.event_type,
                handler=failed_handler,
                attempts=attempts,
                dead_lettered=attempts >= MAX_ATTEMPTS,
            )
            continue
        fan_out = await session.execute(
            _FAN_OUT,
            {
                "tenant_id": event.tenant_id,
                "event_id": event.id,
                "type": event.event_type,
                "now": now,
            },
        )
        delivery_ids: list[uuid.UUID] = list(fan_out.scalars().all())
        await session.execute(
            text(
                "UPDATE platform.outbox_events SET published_at = :now "
                "WHERE tenant_id = :t AND id = :id"
            ),
            {"now": now, "t": event.tenant_id, "id": event.id},
        )
        result.published += 1
        result.deliveries.extend((event.tenant_id, d) for d in delivery_ids)
    return result


async def _run_handlers(database: Database, event: OutboxEvent) -> str | None:
    """Run every handler for the event; the name of the first one that raised, or None."""
    for name, handler in EVENT_HANDLERS.handlers_for(event.event_type):
        ctx = RequestContext(ActorType.WORKER, event.tenant_id, request_id=f"event:{event.id}")
        try:
            async with database.tenant_session(ctx) as tenant_session:
                await handler(tenant_session, event)
        except Exception as exc:  # noqa: BLE001 - recorded and retried; the type is all we log
            log.error(
                "event handler raised",
                handler=name,
                event_id=str(event.id),
                error=type(exc).__name__,
            )
            return name
    return None
