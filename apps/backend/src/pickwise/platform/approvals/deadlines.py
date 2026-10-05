# SPDX-License-Identifier: AGPL-3.0-only
"""Reminders and escalation of approval tasks that wait too long.

A step may set ``escalate_after_hours``; its tasks then carry a ``due_at``. Half way to
it the assignee gets a reminder, and once it passes the task is marked ``escalated`` and
a fallback approver gets a new task: the step's own ``fallback`` if it names one, else the
assignee's manager (``skip_level``), else everyone holding the ``tenant_admin`` role.
Fallback tasks have no deadline, so escalation happens once. If nobody can be found the
task stays with its assignee and stops escalating; that is logged, because an
approval must never pass by itself.

The worker calls ``process_deadlines`` per tenant, in a tenant session, so the approver
resolvers run under that tenant's RLS like everywhere else.
"""

import datetime
import uuid
from dataclasses import replace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit
from pickwise.platform.approvals import types
from pickwise.platform.approvals.service import (
    Request,
    assign,
    base_context,
    get_request,
    resolve_approvers,
)
from pickwise.platform.crypto import KeyEncryptionKey
from pickwise.platform.events import emit_event
from pickwise.platform.notifications.email import QueuedEmail
from pickwise.platform.notifications.service import notify
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database, ops_task
from pickwise.shared.logging import get_logger

log = get_logger(__name__)

DEFAULT_FALLBACK = ("skip_level", "role:tenant_admin")
BATCH = 100


async def tenants_with_deadlines(session: AsyncSession, now: datetime.datetime) -> list[uuid.UUID]:
    """Tenants that have a task to remind or escalate. Ops session."""
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT tenant_id FROM platform.approval_tasks "
                "WHERE status = 'pending' AND due_at IS NOT NULL "
                "  AND (due_at <= :now OR (reminded_at IS NULL "
                "       AND :now >= created_at + (due_at - created_at) / 2))"
            ),
            {"now": now},
        )
    ).all()
    return [r[0] for r in rows]


@ops_task
async def run_deadlines(
    database: Database, kek: KeyEncryptionKey, now: datetime.datetime | None = None
) -> list[QueuedEmail]:
    """What the worker runs every minute: find tenants with something due (ops session),
    then handle each in its own tenant session. Returns the emails to dispatch."""
    now = now or datetime.datetime.now(datetime.UTC)
    async with database.ops_session() as session:
        tenants = await tenants_with_deadlines(session, now)
    emails: list[QueuedEmail] = []
    for tenant_id in tenants:
        ctx = RequestContext(
            ActorType.WORKER, tenant_id, request_id=f"approval-deadlines:{tenant_id}"
        )
        async with database.tenant_session(ctx) as db:
            emails += await process_deadlines(db, kek, tenant_id, now)
    return emails


async def process_deadlines(
    db: AsyncSession, kek: KeyEncryptionKey, tenant_id: uuid.UUID, now: datetime.datetime
) -> list[QueuedEmail]:
    """Escalate overdue tasks, then remind about those past half way. Tenant session."""
    emails = await _escalate(db, kek, tenant_id, now)
    emails += await _remind(db, kek, tenant_id, now)
    return emails


async def _escalate(
    db: AsyncSession, kek: KeyEncryptionKey, tenant_id: uuid.UUID, now: datetime.datetime
) -> list[QueuedEmail]:
    due = (
        await db.execute(
            text(
                "SELECT t.id, t.approval_request_id FROM platform.approval_tasks t "
                "JOIN platform.approval_requests r "
                "  ON r.tenant_id = t.tenant_id AND r.id = t.approval_request_id "
                "WHERE t.status = 'pending' AND t.due_at <= :now AND r.status = 'pending' "
                "ORDER BY t.due_at LIMIT :n"
            ),
            {"now": now, "n": BATCH},
        )
    ).all()
    emails: list[QueuedEmail] = []
    for task_id, request_id in due:
        # Lock the request first, like a decision does, then re-check the task: a decision
        # may have landed since the list was read.
        req = await get_request(db, request_id, lock=True)
        task = (
            await db.execute(
                text(
                    "SELECT assignee_user_id, step_no FROM platform.approval_tasks "
                    "WHERE id = :id AND status = 'pending' AND due_at <= :now"
                ),
                {"id": task_id, "now": now},
            )
        ).one_or_none()
        if task is None or req.status != "pending" or task.step_no != req.current_step:
            continue
        emails += await _escalate_task(db, kek, tenant_id, req, task_id, task.assignee_user_id, now)
    return emails


async def _escalate_task(
    db: AsyncSession,
    kek: KeyEncryptionKey,
    tenant_id: uuid.UUID,
    req: Request,
    task_id: uuid.UUID,
    assignee: uuid.UUID,
    now: datetime.datetime,
) -> list[QueuedEmail]:
    step_no = req.current_step
    spec = req.steps[step_no - 1]
    taken = {
        r[0]
        for r in (
            await db.execute(
                text(
                    "SELECT assignee_user_id FROM platform.approval_tasks "
                    "WHERE approval_request_id = :r AND step_no = :s"
                ),
                {"r": req.id, "s": step_no},
            )
        ).all()
    }
    base = base_context(tenant_id, req)
    fallback_specs = [spec.fallback] if spec.fallback else list(DEFAULT_FALLBACK)
    chosen: list[uuid.UUID] = []
    for fallback in fallback_specs:
        found = await resolve_approvers(
            db,
            [fallback],
            replace(base, escalating_from=assignee),
        )
        chosen = [
            u
            for u in found
            if u not in taken and (spec.allow_self_approval or u != req.requested_by)
        ]
        if chosen:
            break
    if not chosen:
        await db.execute(
            text("UPDATE platform.approval_tasks SET due_at = NULL WHERE id = :id"),
            {"id": task_id},
        )
        log.warning(
            "approval escalation found nobody to hand over to",
            request_id=str(req.id),
            step=step_no,
        )
        return []
    await db.execute(
        text("UPDATE platform.approval_tasks SET status = 'escalated' WHERE id = :id"),
        {"id": task_id},
    )
    pending = await assign(db, req, step_no, chosen, spec=spec, due_at=None, now=now, taken=taken)
    await emit_event(
        db,
        aggregate_type="approval_request",
        aggregate_id=req.id,
        event_type="approval.escalated",
        payload={"entity_type": req.entity_type, "entity_id": str(req.entity_id)},
    )
    await audit.record(
        db,
        "approval.escalated",
        "platform.approval_requests",
        req.id,
        {"step": step_no, "entity_type": req.entity_type},
    )
    label = types.label(req.entity_type)
    emails = await notify(
        db,
        kek,
        tenant_id=tenant_id,
        user_ids=pending,
        type_key="approval.escalated",
        title=f"{label} escalated to you",
        body=f"Step: {spec.name}. It wasn't answered in time.",
        link=f"/approvals/{req.id}",
        entity_type="approval_request",
        entity_id=req.id,
    )
    emails += await notify(
        db,
        kek,
        tenant_id=tenant_id,
        user_ids=[assignee],
        type_key="approval.escalated",
        title=f"{label} escalated",
        body="It wasn't answered in time and has been passed on.",
        link=f"/approvals/{req.id}",
        entity_type="approval_request",
        entity_id=req.id,
    )
    return emails


async def _remind(
    db: AsyncSession, kek: KeyEncryptionKey, tenant_id: uuid.UUID, now: datetime.datetime
) -> list[QueuedEmail]:
    rows = (
        await db.execute(
            text(
                "SELECT t.id, t.approval_request_id, t.assignee_user_id, r.entity_type "
                "FROM platform.approval_tasks t "
                "JOIN platform.approval_requests r "
                "  ON r.tenant_id = t.tenant_id AND r.id = t.approval_request_id "
                "WHERE t.status = 'pending' AND t.due_at IS NOT NULL AND t.reminded_at IS NULL "
                "  AND :now < t.due_at AND r.status = 'pending' "
                "  AND :now >= t.created_at + (t.due_at - t.created_at) / 2 "
                "ORDER BY t.due_at LIMIT :n FOR UPDATE OF t SKIP LOCKED"
            ),
            {"now": now, "n": BATCH},
        )
    ).all()
    emails: list[QueuedEmail] = []
    for task_id, request_id, assignee, entity_type in rows:
        await db.execute(
            text("UPDATE platform.approval_tasks SET reminded_at = :now WHERE id = :id"),
            {"now": now, "id": task_id},
        )
        emails += await notify(
            db,
            kek,
            tenant_id=tenant_id,
            user_ids=[assignee],
            type_key="approval.reminder",
            title=f"Reminder: {types.label(entity_type)} is waiting for you",
            link=f"/approvals/{request_id}",
            entity_type="approval_request",
            entity_id=request_id,
        )
    return emails
