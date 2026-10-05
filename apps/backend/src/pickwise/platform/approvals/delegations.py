# SPDX-License-Identifier: AGPL-3.0-only
"""Delegations: while one is active, new approval tasks for ``from_user`` go to ``to_user``."""

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.approvals.registry import ENTITY_TYPES
from pickwise.shared.errors import NotFoundError, UnprocessableError

MAX_DAYS = 366
_COLUMNS = "id, from_user_id, to_user_id, lower(valid_during), upper(valid_during), entity_types"


@dataclass(frozen=True, slots=True)
class Delegation:
    id: uuid.UUID
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID
    starts_at: datetime.datetime
    ends_at: datetime.datetime
    entity_types: list[str]


async def active_delegate(
    db: AsyncSession, from_user: uuid.UUID, entity_type: str, now: datetime.datetime
) -> uuid.UUID | None:
    """Who covers for ``from_user`` right now for this kind of request. No chaining: a
    delegate who has delegated onward still receives the task."""
    row = (
        await db.execute(
            text(
                "SELECT d.to_user_id FROM platform.delegations d "
                "JOIN platform.memberships m ON m.user_id = d.to_user_id AND m.status = 'active' "
                "WHERE d.from_user_id = :u AND d.valid_during @> CAST(:now AS timestamptz) "
                "  AND (cardinality(d.entity_types) = 0 OR :etype = ANY (d.entity_types)) "
                "ORDER BY lower(d.valid_during) DESC, d.id DESC LIMIT 1"
            ),
            {"u": from_user, "now": now, "etype": entity_type},
        )
    ).first()
    return None if row is None else row[0]


async def create_delegation(
    db: AsyncSession,
    *,
    from_user: uuid.UUID,
    to_user: uuid.UUID,
    starts_at: datetime.datetime,
    ends_at: datetime.datetime,
    entity_types: list[str],
) -> Delegation:
    if from_user == to_user:
        raise UnprocessableError("You can't delegate to yourself.", code="invalid_delegate")
    if ends_at <= starts_at:
        raise UnprocessableError("The delegation must end after it starts.", code="invalid_period")
    if ends_at - starts_at > datetime.timedelta(days=MAX_DAYS):
        raise UnprocessableError(
            f"A delegation can last at most {MAX_DAYS} days.", code="invalid_period"
        )
    unknown = set(entity_types) - set(ENTITY_TYPES)
    if unknown:
        raise UnprocessableError("Unknown request type.", code="unknown_entity_type")
    active = (
        await db.execute(
            text("SELECT 1 FROM platform.memberships WHERE user_id = :u AND status = 'active'"),
            {"u": to_user},
        )
    ).first()
    if active is None:
        raise UnprocessableError("That person can't receive approvals.", code="invalid_delegate")
    row = (
        await db.execute(
            text(
                "INSERT INTO platform.delegations "  # noqa: S608
                "(from_user_id, to_user_id, valid_during, entity_types) "
                "VALUES (:f, :t, tstzrange(:s, :e, '[)'), :types) "
                f"RETURNING {_COLUMNS}"
            ),
            {"f": from_user, "t": to_user, "s": starts_at, "e": ends_at, "types": entity_types},
        )
    ).one()
    return Delegation(*row)


async def list_delegations(
    db: AsyncSession, *, user_id: uuid.UUID | None, include_ended: bool = False
) -> list[Delegation]:
    """Delegations from ``user_id`` (or everyone's when None), newest first."""
    rows = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM platform.delegations "  # noqa: S608
                "WHERE (CAST(:u AS uuid) IS NULL OR from_user_id = :u) "
                "  AND (:ended OR upper(valid_during) > now()) "
                "ORDER BY lower(valid_during) DESC, id DESC LIMIT 200"
            ),
            {"u": user_id, "ended": include_ended},
        )
    ).all()
    return [Delegation(*r) for r in rows]


async def end_delegation(
    db: AsyncSession,
    delegation_id: uuid.UUID,
    *,
    only_for_user: uuid.UUID | None,
    now: datetime.datetime,
) -> Delegation:
    """End a delegation now. One that hasn't started is removed; a running one is cut short
    and kept as history."""
    row = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM platform.delegations "  # noqa: S608
                "WHERE id = :id AND (CAST(:u AS uuid) IS NULL OR from_user_id = :u) "
                "FOR UPDATE"
            ),
            {"id": delegation_id, "u": only_for_user},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Delegation not found.")
    current = Delegation(*row)
    if current.ends_at <= now:
        return current
    if current.starts_at > now:
        await db.execute(
            text("DELETE FROM platform.delegations WHERE id = :id"), {"id": delegation_id}
        )
        return current
    ended = (
        await db.execute(
            text(
                "UPDATE platform.delegations "  # noqa: S608
                "SET valid_during = tstzrange(lower(valid_during), :end, '[)') "
                f"WHERE id = :id RETURNING {_COLUMNS}"
            ),
            {"id": delegation_id, "end": now},
        )
    ).one()
    return Delegation(*ended)
