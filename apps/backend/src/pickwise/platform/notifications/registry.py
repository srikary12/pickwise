# SPDX-License-Identifier: AGPL-3.0-only
"""Notification types: which events exist and which channels they use by default.

A module registers its types at import (leave: ``leave.request.submitted``, …). Members
can switch channels per type in ``memberships.notification_preferences``; the type's
``channels`` are the defaults, and ``locked`` channels can't be switched off.
"""

from dataclasses import dataclass

CHANNELS = ("in_app", "email")


@dataclass(frozen=True, slots=True)
class NotificationType:
    key: str
    label: str
    description: str
    channels: tuple[str, ...] = ("in_app", "email")
    # Channels the member can't turn off (e.g. security notices).
    locked: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for channel in (*self.channels, *self.locked):
            if channel not in CHANNELS:
                raise ValueError(f"unknown notification channel {channel!r}")


class NotificationTypeRegistry:
    def __init__(self) -> None:
        self._types: dict[str, NotificationType] = {}

    def register(self, *types: NotificationType) -> None:
        for t in types:
            existing = self._types.get(t.key)
            if existing is not None and existing != t:
                raise ValueError(f"notification type {t.key} registered twice")
            self._types[t.key] = t

    def get(self, key: str) -> NotificationType | None:
        return self._types.get(key)

    def all(self) -> list[NotificationType]:
        return sorted(self._types.values(), key=lambda t: t.key)


NOTIFICATION_TYPES = NotificationTypeRegistry()

NOTIFICATION_TYPES.register(
    NotificationType(
        "system.notice",
        "Announcements",
        "Messages from your administrators and the system",
    ),
)
