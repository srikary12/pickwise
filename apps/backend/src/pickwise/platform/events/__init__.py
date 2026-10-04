# SPDX-License-Identifier: AGPL-3.0-only
"""Transactional event outbox: modules emit events, the relay publishes them.

``emit_event`` writes ``platform.outbox_events`` inside the caller's transaction, so an
event exists if and only if the change that caused it committed. The relay (a worker
task) then runs in-process handlers and queues webhook deliveries.
"""

from pickwise.platform.events.registry import (
    EVENT_HANDLERS,
    EventHandler,
    EventHandlerRegistry,
    OutboxEvent,
)
from pickwise.platform.events.service import emit_event, kick_relay

__all__ = [
    "EVENT_HANDLERS",
    "EventHandler",
    "EventHandlerRegistry",
    "OutboxEvent",
    "emit_event",
    "kick_relay",
]
