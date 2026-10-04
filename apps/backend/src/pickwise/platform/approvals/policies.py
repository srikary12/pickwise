# SPDX-License-Identifier: AGPL-3.0-only
"""Approval policy definitions (CRUD). A running request keeps the snapshot it started with,
so editing or archiving a policy never changes approvals already in flight."""

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.shared.errors import ConflictError, NotFoundError

_COLUMNS = (
    "id, key, entity_type, name, priority, conditions, steps, is_active, archived_at, "
    "created_at, row_version"
)


@dataclass(frozen=True, slots=True)
class PolicyRow:
    id: uuid.UUID
    key: str
    entity_type: str
    name: str
    priority: int
    conditions: dict[str, Any]
    steps: list[dict[str, Any]]
    is_active: bool
    archived_at: datetime.datetime | None
    created_at: datetime.datetime
    row_version: int


_BINDS = (bindparam("conditions", type_=JSONB), bindparam("steps", type_=JSONB))


async def list_policies(
    db: AsyncSession, *, entity_type: str | None, include_archived: bool
) -> list[PolicyRow]:
    rows = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM platform.approval_policies "  # noqa: S608
                "WHERE (CAST(:e AS text) IS NULL OR entity_type = :e) "
                "  AND (:archived OR archived_at IS NULL) "
                "ORDER BY entity_type, priority DESC, key"
            ),
            {"e": entity_type, "archived": include_archived},
        )
    ).all()
    return [PolicyRow(*r) for r in rows]


async def get_policy(db: AsyncSession, policy_id: uuid.UUID) -> PolicyRow:
    row = (
        await db.execute(
            text(f"SELECT {_COLUMNS} FROM platform.approval_policies WHERE id = :id"),  # noqa: S608
            {"id": policy_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Approval policy not found.")
    return PolicyRow(*row)


async def create_policy(
    db: AsyncSession,
    *,
    key: str,
    entity_type: str,
    name: str,
    priority: int,
    conditions: dict[str, Any],
    steps: list[dict[str, Any]],
    is_active: bool,
) -> PolicyRow:
    try:
        row = (
            await db.execute(
                text(
                    "INSERT INTO platform.approval_policies "  # noqa: S608
                    "(key, entity_type, name, priority, conditions, steps, is_active) "
                    "VALUES (:key, :e, :name, :priority, :conditions, :steps, :active) "
                    f"RETURNING {_COLUMNS}"
                ).bindparams(*_BINDS),
                {
                    "key": key,
                    "e": entity_type,
                    "name": name,
                    "priority": priority,
                    "conditions": conditions,
                    "steps": steps,
                    "active": is_active,
                },
            )
        ).one()
    except IntegrityError as exc:  # the only constraint a valid insert can hit is the key
        raise ConflictError("A policy with that key exists.", code="key_taken") from exc
    return PolicyRow(*row)


async def update_policy(
    db: AsyncSession,
    policy_id: uuid.UUID,
    *,
    name: str,
    priority: int,
    conditions: dict[str, Any],
    steps: list[dict[str, Any]],
    is_active: bool,
    row_version: int,
) -> PolicyRow:
    """The key and entity type are fixed: they identify the policy in reports and audit."""
    await get_policy(db, policy_id)
    row = (
        await db.execute(
            text(
                "UPDATE platform.approval_policies SET name = :name, priority = :priority, "  # noqa: S608
                "conditions = :conditions, steps = :steps, is_active = :active "
                f"WHERE id = :id AND row_version = :v RETURNING {_COLUMNS}"
            ).bindparams(*_BINDS),
            {
                "name": name,
                "priority": priority,
                "conditions": conditions,
                "steps": steps,
                "active": is_active,
                "id": policy_id,
                "v": row_version,
            },
        )
    ).one_or_none()
    if row is None:
        raise ConflictError(
            "Someone else changed this policy. Reload and try again.", code="stale_row_version"
        )
    return PolicyRow(*row)


async def set_archived(db: AsyncSession, policy_id: uuid.UUID, archived: bool) -> PolicyRow:
    current = await get_policy(db, policy_id)
    if archived == (current.archived_at is not None):
        return current
    try:
        row = (
            await db.execute(
                text(
                    "UPDATE platform.approval_policies SET archived_at = "  # noqa: S608
                    f"CASE WHEN :a THEN now() END WHERE id = :id RETURNING {_COLUMNS}"
                ),
                {"a": archived, "id": policy_id},
            )
        ).one()
    except IntegrityError as exc:  # an active policy took the key meanwhile
        raise ConflictError("Another active policy uses that key.", code="key_taken") from exc
    return PolicyRow(*row)
