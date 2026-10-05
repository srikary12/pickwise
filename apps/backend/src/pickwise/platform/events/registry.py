# SPDX-License-Identifier: AGPL-3.0-only
"""In-process event handlers.

A module registers a handler for an exact event type (``leave.request.approved``) or a
prefix (``leave.*``). The relay calls it in a tenant session with ``actor_type =
'worker'``, after the emitting transaction committed. Delivery is at-least-once: a
handler must be idempotent, and when it raises the relay retries the whole event.
Information that flows against the module import direction travels this way
(CLAUDE.md "Module boundaries").
"""

import datetime
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

EVENT_TYPE_PATTERN = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
# What an endpoint or handler may subscribe to: a type, or a prefix ending in ".*".
SUBSCRIPTION_PATTERN = re.compile(r"^[a-z_]+(\.[a-z_]+)*(\.\*)?$")


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: uuid.UUID
    tenant_id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime.datetime


EventHandler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]


def matches(subscription: str, event_type: str) -> bool:
    if subscription.endswith(".*"):
        return event_type.startswith(subscription[:-1])
    return subscription == event_type


@dataclass(frozen=True, slots=True)
class _Registration:
    name: str
    subscription: str
    handler: EventHandler


class EventHandlerRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(self, name: str, subscription: str, handler: EventHandler) -> None:
        """``name`` identifies the handler in logs and keeps registration idempotent."""
        if not SUBSCRIPTION_PATTERN.match(subscription):
            raise ValueError(f"bad event subscription {subscription!r}")
        existing = self._registrations.get(name)
        if existing is not None and (existing.subscription, existing.handler) != (
            subscription,
            handler,
        ):
            raise ValueError(f"event handler {name} registered twice")
        self._registrations[name] = _Registration(name, subscription, handler)

    def unregister(self, name: str) -> None:
        self._registrations.pop(name, None)

    def handlers_for(self, event_type: str) -> list[tuple[str, EventHandler]]:
        return [
            (r.name, r.handler)
            for r in sorted(self._registrations.values(), key=lambda r: r.name)
            if matches(r.subscription, event_type)
        ]


EVENT_HANDLERS = EventHandlerRegistry()
