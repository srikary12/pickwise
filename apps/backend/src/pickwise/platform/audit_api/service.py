# SPDX-License-Identifier: AGPL-3.0-only
"""Querying ``audit.events`` with keyset pagination (newest first)."""

import base64
import datetime
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.shared.errors import UnprocessableError

MAX_EXPORT_ROWS = 100_000
_COLUMNS = (
    "e.id, e.occurred_at, e.actor_user_id, a.display_name AS actor_name, e.actor_type, "
    "e.action, e.entity_schema, e.entity_table, e.entity_id, e.changes, e.request_id, "
    "host(e.ip) AS ip"
)


@dataclass(frozen=True, slots=True)
class AuditFilter:
    entity_table: str | None = None  # 'schema.table'
    entity_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    action: str | None = None  # exact, or "prefix.*"
    request_id: str | None = None
    since: datetime.datetime | None = None
    until: datetime.datetime | None = None

    def as_details(self) -> dict[str, str]:
        """The filters that were set, for the audit record of an export (ids and dates only)."""
        values = {name: getattr(self, name) for name in self.__slots__}
        return {name: str(value) for name, value in values.items() if value is not None}


_WHERE = (
    "WHERE (CAST(:entity AS text) IS NULL OR (e.entity_schema || '.' || e.entity_table) = :entity) "
    "  AND (CAST(:entity_id AS uuid) IS NULL OR e.entity_id = :entity_id) "
    "  AND (CAST(:actor AS uuid) IS NULL OR e.actor_user_id = :actor) "
    "  AND (CAST(:action AS text) IS NULL OR e.action = :action "
    "       OR (:action LIKE '%.*' AND starts_with(e.action, left(:action, -1)))) "
    "  AND (CAST(:request AS text) IS NULL OR e.request_id = :request) "
    "  AND (CAST(:since AS timestamptz) IS NULL OR e.occurred_at >= :since) "
    "  AND (CAST(:until AS timestamptz) IS NULL OR e.occurred_at < :until) "
)
_JOIN = (
    "FROM audit.events e LEFT JOIN (SELECT m.user_id, u.display_name FROM platform.memberships m "
    "JOIN platform.users u ON u.id = m.user_id) a ON a.user_id = e.actor_user_id "
)


def _params(f: AuditFilter) -> dict[str, Any]:
    return {
        "entity": f.entity_table,
        "entity_id": f.entity_id,
        "actor": f.actor_user_id,
        "action": f.action,
        "request": f.request_id,
        "since": f.since,
        "until": f.until,
    }


def encode_cursor(occurred_at: datetime.datetime, event_id: uuid.UUID) -> str:
    raw = f"{occurred_at.isoformat()}|{event_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, _, ident = raw.partition("|")
        return datetime.datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnprocessableError("That cursor isn't valid.", code="invalid_cursor") from exc


async def page(
    db: AsyncSession, f: AuditFilter, *, cursor: str | None, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    after = decode_cursor(cursor) if cursor else None
    rows = (
        (
            await db.execute(
                text(
                    f"SELECT {_COLUMNS} {_JOIN} {_WHERE} "
                    "  AND (CAST(:c_at AS timestamptz) IS NULL "
                    "       OR (e.occurred_at, e.id) < "
                    "          (CAST(:c_at AS timestamptz), CAST(:c_id AS uuid))) "
                    "ORDER BY e.occurred_at DESC, e.id DESC LIMIT :n"
                ),
                {
                    **_params(f),
                    "c_at": after[0] if after else None,
                    "c_id": after[1] if after else None,
                    "n": limit + 1,
                },
            )
        )
        .mappings()
        .all()
    )
    items = [dict(r) for r in rows]
    more = len(items) > limit
    items = items[:limit]
    next_cursor = encode_cursor(items[-1]["occurred_at"], items[-1]["id"]) if more else None
    return items, next_cursor


async def count(db: AsyncSession, f: AuditFilter) -> int:
    total: int = (
        await db.execute(text(f"SELECT count(*) {_JOIN} {_WHERE}"), _params(f))
    ).scalar_one()
    return total
