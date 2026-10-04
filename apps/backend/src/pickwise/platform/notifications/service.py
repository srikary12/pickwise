# SPDX-License-Identifier: AGPL-3.0-only
"""Creating notifications: an in-app row and/or a queued email, per the member's preferences.

Call ``notify`` inside the transaction that caused the event. It returns the queued emails;
pass them to ``notifications.email.dispatch`` after the commit (the sweep covers misses).
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.crypto import KeyEncryptionKey, load_tenant_keyring
from pickwise.platform.notifications.email import QueuedEmail, queue_email
from pickwise.platform.notifications.registry import (
    CHANNELS,
    NOTIFICATION_TYPES,
    NotificationType,
)
from pickwise.shared.errors import UnprocessableError


def effective_channels(ntype: NotificationType, preferences: dict[str, Any]) -> set[str]:
    """The channels to use: the member's choice per type, else the type's defaults.
    Locked channels stay on whatever the member chose."""
    chosen = preferences.get(ntype.key)
    channels = set(ntype.channels)
    if isinstance(chosen, dict):
        channels = {c for c in CHANNELS if chosen.get(c, c in ntype.channels) is True}
    return channels | set(ntype.locked)


def _check_link(link: str | None) -> None:
    # Relative app paths only: a notification must not be a way to send people elsewhere.
    if link is not None and not (link.startswith("/") and not link.startswith("//")):
        raise ValueError("notification links must be app paths starting with '/'")


async def notify(
    db: AsyncSession,
    kek: KeyEncryptionKey,
    *,
    tenant_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    type_key: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> list[QueuedEmail]:
    ntype = NOTIFICATION_TYPES.get(type_key)
    if ntype is None:
        raise ValueError(f"unknown notification type {type_key!r}")
    _check_link(link)
    if not user_ids:
        return []
    members = (
        await db.execute(
            text(
                "SELECT m.user_id, m.notification_preferences, u.email, u.locale "
                "FROM platform.memberships m JOIN platform.users u ON u.id = m.user_id "
                "WHERE m.user_id = ANY(:ids) AND m.status = 'active' AND u.status = 'active'"
            ),
            {"ids": list(dict.fromkeys(user_ids))},
        )
    ).all()
    emails: list[QueuedEmail] = []
    keyring = None
    for user_id, preferences, address, locale in members:
        channels = effective_channels(ntype, preferences or {})
        if "in_app" in channels:
            await db.execute(
                text(
                    "INSERT INTO platform.notifications "
                    "(user_id, type, title, body, link, entity_type, entity_id) "
                    "VALUES (:u, :t, :title, :body, :link, :et, :ei)"
                ),
                {
                    "u": user_id,
                    "t": type_key,
                    "title": title[:300],
                    "body": body,
                    "link": link,
                    "et": entity_type,
                    "ei": entity_id,
                },
            )
        if "email" in channels:
            keyring = keyring or await load_tenant_keyring(db, kek, tenant_id)
            # Title and body can contain personal data: they travel encrypted, like link tokens.
            emails.append(
                await queue_email(
                    db,
                    keyring,
                    tenant_id=tenant_id,
                    to_address=str(address),
                    template_key="notification",
                    variables={},
                    secrets={"title": title, "body": body or "", "link": link or ""},
                    locale=str(locale),
                )
            )
    return emails


async def own_preferences(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    row: dict[str, Any] | None = (
        await db.execute(
            text(
                "SELECT notification_preferences FROM platform.memberships "
                "WHERE user_id = :u AND status = 'active'"
            ),
            {"u": user_id},
        )
    ).scalar_one_or_none()
    return row or {}


async def save_preferences(
    db: AsyncSession, user_id: uuid.UUID, preferences: dict[str, dict[str, bool]]
) -> None:
    for key, channels in preferences.items():
        ntype = NOTIFICATION_TYPES.get(key)
        if ntype is None:
            raise UnprocessableError(f"Unknown notification type {key!r}.", code="unknown_type")
        for locked in ntype.locked:
            if channels.get(locked) is False:
                raise UnprocessableError(
                    f"{ntype.label} can't be turned off on {locked}.", code="channel_locked"
                )
    from sqlalchemy import bindparam
    from sqlalchemy.dialects.postgresql import JSONB

    await db.execute(
        text(
            "UPDATE platform.memberships SET notification_preferences = :p "
            "WHERE user_id = :u AND status = 'active'"
        ).bindparams(bindparam("p", type_=JSONB)),
        {"p": preferences, "u": user_id},
    )
