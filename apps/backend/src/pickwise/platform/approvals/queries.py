# SPDX-License-Identifier: AGPL-3.0-only
"""Read models for the approvals API: the inbox, a request with its steps and tasks."""

import uuid
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.approvals.service import Request, get_request
from pickwise.shared.errors import NotFoundError

InboxState = Literal["pending", "decided", "all"]

_PERSON = (
    "LEFT JOIN (SELECT m.user_id, u.display_name FROM platform.memberships m "
    "JOIN platform.users u ON u.id = m.user_id) {alias} ON {alias}.user_id = {column}"
)


def _person(alias: str, column: str) -> str:
    return _PERSON.format(alias=alias, column=column)


async def inbox(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    state: InboxState,
    before: uuid.UUID | None,
    limit: int,
) -> tuple[list[dict[str, Any]], uuid.UUID | None]:
    """Tasks assigned to the user, newest first. ``pending`` (default) is what needs
    action; ``decided`` is what the user already acted on."""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT t.id AS task_id, t.approval_request_id AS request_id, "  # noqa: S608
                    "  t.step_no, "
                    "  t.status AS task_status, t.due_at, t.created_at, t.delegated_from_user_id, "
                    "  r.entity_type, r.entity_id, r.status AS request_status, r.requested_by, "
                    "  requester.display_name AS requester_name, "
                    "  r.policy_snapshot ->> 'name' AS policy_name, "
                    "  r.policy_snapshot -> 'steps' -> (t.step_no - 1) ->> 'name' AS step_name "
                    "FROM platform.approval_tasks t "
                    "JOIN platform.approval_requests r "
                    "  ON r.tenant_id = t.tenant_id AND r.id = t.approval_request_id "
                    f"{_person('requester', 'r.requested_by')} "
                    "WHERE t.assignee_user_id = :u "
                    "  AND (CASE :state "
                    "         WHEN 'pending' THEN t.status = 'pending' "
                    "         WHEN 'decided' THEN t.status IN ('approved', 'rejected') "
                    "         ELSE t.status NOT IN ('delegated') END) "
                    "  AND (CAST(:before AS uuid) IS NULL OR t.id < :before) "
                    "ORDER BY t.id DESC LIMIT :n"
                ),
                {"u": user_id, "state": state, "before": before, "n": limit + 1},
            )
        )
        .mappings()
        .all()
    )
    items = [dict(r) for r in rows]
    more = len(items) > limit
    return items[:limit], items[limit - 1]["task_id"] if more else None


async def pending_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    count: int = (
        await db.execute(
            text(
                "SELECT count(*) FROM platform.approval_tasks "
                "WHERE assignee_user_id = :u AND status = 'pending'"
            ),
            {"u": user_id},
        )
    ).scalar_one()
    return count


async def requested_by(
    db: AsyncSession, user_id: uuid.UUID, *, before: uuid.UUID | None, limit: int
) -> tuple[list[dict[str, Any]], uuid.UUID | None]:
    """Requests the user started, newest first."""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT r.id AS request_id, r.entity_type, r.entity_id, r.status, "
                    "  r.current_step, "
                    "  jsonb_array_length(r.policy_snapshot -> 'steps') AS step_count, "
                    "  r.created_at, r.completed_at, "
                    "  r.policy_snapshot ->> 'name' AS policy_name "
                    "FROM platform.approval_requests r "
                    "WHERE r.requested_by = :u "
                    "  AND (CAST(:before AS uuid) IS NULL OR r.id < :before) "
                    "ORDER BY r.id DESC LIMIT :n"
                ),
                {"u": user_id, "before": before, "n": limit + 1},
            )
        )
        .mappings()
        .all()
    )
    items = [dict(r) for r in rows]
    more = len(items) > limit
    return items[:limit], items[limit - 1]["request_id"] if more else None


async def detail(
    db: AsyncSession, request_id: uuid.UUID, viewer: uuid.UUID | None, *, can_view_all: bool
) -> tuple[Request, dict[str, Any], list[dict[str, Any]]]:
    """The request, its requester and every task. 404 unless the viewer is the requester,
    an assignee (including a delegated or escalated one) or may view all."""
    req = await get_request(db, request_id)
    if not can_view_all and (viewer is None or req.requested_by != viewer):
        mine = (
            await db.execute(
                text(
                    "SELECT 1 FROM platform.approval_tasks "
                    "WHERE approval_request_id = :r AND assignee_user_id = :u LIMIT 1"
                ),
                {"r": request_id, "u": viewer},
            )
        ).first()
        if mine is None:
            raise NotFoundError("Approval not found.")
    requester = (
        (
            await db.execute(
                text(
                    "SELECT m.user_id AS id, u.display_name AS name "
                    "FROM platform.memberships m JOIN platform.users u ON u.id = m.user_id "
                    "WHERE m.user_id = :u"
                ),
                {"u": req.requested_by},
            )
        )
        .mappings()
        .one_or_none()
    )
    tasks = (
        (
            await db.execute(
                text(
                    "SELECT t.id, t.step_no, t.assignee_user_id, "  # noqa: S608
                    "  assignee.display_name AS assignee_name, t.status, t.acted_at, t.comment, "
                    "  t.delegated_from_user_id, t.due_at, t.created_at "
                    "FROM platform.approval_tasks t "
                    f"{_person('assignee', 't.assignee_user_id')} "
                    "WHERE t.approval_request_id = :r ORDER BY t.step_no, t.created_at, t.id"
                ),
                {"r": request_id},
            )
        )
        .mappings()
        .all()
    )
    return req, dict(requester) if requester else {}, [dict(t) for t in tasks]
