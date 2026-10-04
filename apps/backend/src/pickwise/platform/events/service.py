# SPDX-License-Identifier: AGPL-3.0-only
"""Emitting events (in the caller's transaction) and nudging the relay (after commit)."""

import uuid
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.events.registry import EVENT_TYPE_PATTERN
from pickwise.platform.jobs import JobQueueUnavailableError, defer
from pickwise.shared.logging import get_logger

log = get_logger(__name__)

_INSERT = text(
    "INSERT INTO platform.outbox_events (aggregate_type, aggregate_id, event_type, payload) "
    "VALUES (:aggregate_type, :aggregate_id, :event_type, :payload) RETURNING id"
).bindparams(bindparam("payload", type_=JSONB))


async def emit_event(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Record that something happened, in the current transaction.

    ``payload`` goes to in-process handlers and, signed, to customers' webhook endpoints:
    ids and non-sensitive facts only. Never personal data, salary figures or tokens
    (CLAUDE.md rule 14). Call ``kick_relay`` after the commit.
    """
    if not EVENT_TYPE_PATTERN.match(event_type):
        raise ValueError(
            f"bad event type {event_type!r}: use dotted lowercase, e.g. 'leave.request.approved'"
        )
    event_id: uuid.UUID = (
        await session.execute(
            _INSERT,
            {
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
                "payload": payload or {},
            },
        )
    ).scalar_one()
    return event_id


async def kick_relay() -> None:
    """Ask the worker to publish now instead of at the next minutely sweep. Best effort."""
    try:
        await defer("pickwise.relay_events")
    except JobQueueUnavailableError:
        pass
    except Exception as exc:  # noqa: BLE001 - the sweep covers it; never fail the request
        log.warning("relay kick failed; the sweep will publish", error=type(exc).__name__)
