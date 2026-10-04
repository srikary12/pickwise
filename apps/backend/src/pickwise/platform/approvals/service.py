# SPDX-License-Identifier: AGPL-3.0-only
"""The approvals engine: start a request, assign steps, record decisions.

Everything here runs in the caller's transaction. Modules call ``start_approval`` while
they change their own entity, and the registered handler runs inside the decision's
transaction, so a module's state and its approval can't diverge. Callers pass the
returned emails to ``notifications.email.dispatch`` after the commit (a sweep covers
misses).

Task statuses: ``pending`` (waiting), ``approved`` / ``rejected`` (acted), ``skipped``
(another approver decided the step, or the request closed), ``delegated`` (the original
approver's task while a delegation was active: the delegate got a new task) and
``escalated`` (nobody acted in time: the fallback approver got a new task).
"""

import datetime
import json
import uuid
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit
from pickwise.platform.approvals import types
from pickwise.platform.approvals.conditions import evaluate
from pickwise.platform.approvals.delegations import active_delegate
from pickwise.platform.approvals.policy import StepSpec, parse_steps, split_approver
from pickwise.platform.approvals.registry import (
    APPROVAL_HANDLERS,
    APPROVER_RESOLVERS,
    ENTITY_TYPES,
    ApprovalDecision,
    ResolveContext,
)
from pickwise.platform.crypto import KeyEncryptionKey
from pickwise.platform.events import emit_event
from pickwise.platform.notifications.email import QueuedEmail
from pickwise.platform.notifications.service import notify
from pickwise.shared.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
)
from pickwise.shared.logging import get_logger

log = get_logger(__name__)

MAX_COMMENT_LENGTH = 2000
_COLUMNS = (
    "id, entity_type, entity_id, policy_id, policy_snapshot, status, current_step, "
    "requested_by, attributes, completed_at, created_at, row_version"
)


class NoApprovalPolicyError(UnprocessableError):
    code = "no_approval_policy"


class ApproverUnresolvedError(UnprocessableError):
    code = "approver_unresolved"


@dataclass(frozen=True, slots=True)
class Request:
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    policy_id: uuid.UUID
    snapshot: dict[str, Any]
    status: str
    current_step: int
    requested_by: uuid.UUID | None
    attributes: dict[str, Any]
    completed_at: datetime.datetime | None
    created_at: datetime.datetime
    row_version: int

    @property
    def steps(self) -> list[StepSpec]:
        return parse_steps(self.snapshot["steps"])


@dataclass(frozen=True, slots=True)
class Outcome:
    request: Request
    emails: list[QueuedEmail]


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _jsonable(attributes: dict[str, Any]) -> dict[str, Any]:
    """Attributes as stored and evaluated: JSON types only (decimals and dates as strings)."""
    normalised: dict[str, Any] = json.loads(json.dumps(attributes, default=str))
    return normalised


@dataclass(frozen=True, slots=True)
class _Policy:
    id: uuid.UUID
    key: str
    name: str
    priority: int
    conditions: dict[str, Any]
    steps: list[dict[str, Any]]
    row_version: int


async def find_policy(
    db: AsyncSession, entity_type: str, attributes: dict[str, Any]
) -> _Policy | None:
    """The highest-priority active policy whose conditions match (ties: oldest first)."""
    rows = (
        await db.execute(
            text(
                "SELECT id, key, name, priority, conditions, steps, row_version "
                "FROM platform.approval_policies "
                "WHERE entity_type = :e AND is_active AND archived_at IS NULL "
                "ORDER BY priority DESC, created_at, id"
            ),
            {"e": entity_type},
        )
    ).all()
    for row in rows:
        if evaluate(row.conditions, attributes):
            return _Policy(*row)
    return None


async def get_request(db: AsyncSession, request_id: uuid.UUID, *, lock: bool = False) -> Request:
    row = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM platform.approval_requests WHERE id = :id"  # noqa: S608
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": request_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Approval not found.")
    return Request(*row)


async def _active_members(db: AsyncSession, user_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not user_ids:
        return set()
    rows = (
        await db.execute(
            text(
                "SELECT user_id FROM platform.memberships "
                "WHERE user_id = ANY (:ids) AND status = 'active'"
            ),
            {"ids": user_ids},
        )
    ).all()
    return {r[0] for r in rows}


async def resolve_approvers(
    db: AsyncSession, specs: list[str], base: ResolveContext
) -> list[uuid.UUID]:
    """Users the specs resolve to, in order, active members only. A kind nobody registered
    resolves to nobody."""
    found: list[uuid.UUID] = []
    for spec in specs:
        kind, argument = split_approver(spec)
        resolver = APPROVER_RESOLVERS.get(kind)
        if resolver is None:
            log.warning("no resolver registered for approver kind", kind=kind)
            continue
        found.extend(await resolver(db, replace(base, argument=argument)))
    unique = list(dict.fromkeys(found))
    active = await _active_members(db, unique)
    return [u for u in unique if u in active]


def base_context(tenant_id: uuid.UUID, req: Request) -> ResolveContext:
    return ResolveContext(
        tenant_id=tenant_id,
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        attributes=req.attributes,
        requested_by=req.requested_by,
    )


async def _insert_task(
    db: AsyncSession,
    req: Request,
    step_no: int,
    user_id: uuid.UUID,
    *,
    status: str,
    due_at: datetime.datetime | None,
    now: datetime.datetime,
    delegated_from: uuid.UUID | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO platform.approval_tasks (approval_request_id, step_no, assignee_user_id, "
            "status, delegated_from_user_id, due_at, created_at) "
            "VALUES (:r, :s, :u, :status, :from, :due, :now)"
        ),
        {
            "r": req.id,
            "s": step_no,
            "u": user_id,
            "status": status,
            "from": delegated_from,
            "due": due_at,
            "now": now,
        },
    )


async def assign(
    db: AsyncSession,
    req: Request,
    step_no: int,
    approvers: list[uuid.UUID],
    *,
    spec: StepSpec,
    due_at: datetime.datetime | None,
    now: datetime.datetime,
    taken: set[uuid.UUID],
) -> list[uuid.UUID]:
    """Create the step's tasks and return who must now act. An approver with an active
    delegation keeps a ``delegated`` record and the delegate gets the working task.
    ``taken`` holds people who already have a task at this step, so nobody gets two."""
    pending: list[uuid.UUID] = []
    for approver in approvers:
        if approver in taken:
            continue
        delegate = await active_delegate(db, approver, req.entity_type, now)
        if (
            delegate is not None
            and delegate not in taken
            and (spec.allow_self_approval or delegate != req.requested_by)
        ):
            await _insert_task(db, req, step_no, approver, status="delegated", due_at=None, now=now)
            await _insert_task(
                db,
                req,
                step_no,
                delegate,
                status="pending",
                due_at=due_at,
                now=now,
                delegated_from=approver,
            )
            taken.update({approver, delegate})
            pending.append(delegate)
        else:
            await _insert_task(db, req, step_no, approver, status="pending", due_at=due_at, now=now)
            taken.add(approver)
            pending.append(approver)
    return pending


async def _open_step(
    db: AsyncSession,
    kek: KeyEncryptionKey,
    tenant_id: uuid.UUID,
    req: Request,
    step_no: int,
    now: datetime.datetime,
) -> list[QueuedEmail]:
    spec = req.steps[step_no - 1]
    base = base_context(tenant_id, req)

    async def candidates(specs: list[str]) -> list[uuid.UUID]:
        found = await resolve_approvers(db, specs, base)
        if spec.allow_self_approval:
            return found
        return [u for u in found if u != req.requested_by]

    approvers = await candidates(spec.approvers)
    if not approvers and spec.fallback:
        approvers = await candidates([spec.fallback])
    if not approvers:
        raise ApproverUnresolvedError(
            f"No approver could be found for the step '{spec.name}'. "
            "Check the organisation setup and the approval policy."
        )
    due_at = (
        now + datetime.timedelta(hours=spec.escalate_after_hours)
        if spec.escalate_after_hours
        else None
    )
    pending = await assign(
        db, req, step_no, approvers, spec=spec, due_at=due_at, now=now, taken=set()
    )
    return await notify(
        db,
        kek,
        tenant_id=tenant_id,
        user_ids=pending,
        type_key="approval.assigned",
        title=f"{types.label(req.entity_type)} needs your approval",
        body=f"Step: {spec.name}",
        link=f"/approvals/{req.id}",
        entity_type="approval_request",
        entity_id=req.id,
    )


async def start_approval(
    db: AsyncSession,
    kek: KeyEncryptionKey,
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    attributes: dict[str, Any],
    requested_by: uuid.UUID | None,
    now: datetime.datetime | None = None,
) -> Outcome:
    """Open an approval for a module's entity.

    ``attributes`` decide which policy applies and help resolve approvers (department,
    amount, number of days, …): ids and non-sensitive facts, JSON-safe or convertible with
    ``str``. Raises ``NoApprovalPolicyError`` if no active policy matches and
    ``ApproverUnresolvedError`` if the first step has nobody to ask; both leave nothing
    behind once the caller's transaction rolls back. At most one request per entity may be
    pending.
    """
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"unknown approval entity type {entity_type!r}")
    now = now or _utcnow()
    attributes = _jsonable(attributes)
    policy = await find_policy(db, entity_type, attributes)
    if policy is None:
        raise NoApprovalPolicyError(
            "No approval policy covers this request. Ask an administrator to set one up."
        )
    snapshot = {
        "policy_id": str(policy.id),
        "key": policy.key,
        "name": policy.name,
        "priority": policy.priority,
        "conditions": policy.conditions,
        "steps": policy.steps,
        "policy_row_version": policy.row_version,
    }
    row = (
        await db.execute(
            text(
                "INSERT INTO platform.approval_requests (entity_type, entity_id, policy_id, "  # noqa: S608
                "policy_snapshot, requested_by, attributes, created_at) "
                "VALUES (:e, :id, :p, :snap, :by, :attrs, :now) "
                "ON CONFLICT (tenant_id, entity_type, entity_id) WHERE status = 'pending' "
                f"DO NOTHING RETURNING {_COLUMNS}"
            ).bindparams(bindparam("snap", type_=JSONB), bindparam("attrs", type_=JSONB)),
            {
                "e": entity_type,
                "id": entity_id,
                "p": policy.id,
                "snap": snapshot,
                "by": requested_by,
                "attrs": attributes,
                "now": now,
            },
        )
    ).one_or_none()
    if row is None:
        raise ConflictError("This already has a pending approval.", code="approval_pending")
    req = Request(*row)
    emails = await _open_step(db, kek, tenant_id, req, 1, now)
    await emit_event(
        db,
        aggregate_type="approval_request",
        aggregate_id=req.id,
        event_type="approval.requested",
        payload={"entity_type": entity_type, "entity_id": str(entity_id)},
    )
    await audit.record(
        db,
        "approval.started",
        "platform.approval_requests",
        req.id,
        {"entity_type": entity_type, "policy": policy.key},
    )
    return Outcome(req, emails)


async def _skip_pending(db: AsyncSession, request_id: uuid.UUID, step_no: int | None) -> None:
    await db.execute(
        text(
            "UPDATE platform.approval_tasks SET status = 'skipped' "
            "WHERE approval_request_id = :r AND status = 'pending' "
            "  AND (CAST(:s AS integer) IS NULL OR step_no = :s)"
        ),
        {"r": request_id, "s": step_no},
    )


async def _finalize(
    db: AsyncSession,
    kek: KeyEncryptionKey,
    tenant_id: uuid.UUID,
    req: Request,
    status: str,
    *,
    decided_by: uuid.UUID | None,
    comment: str | None,
    now: datetime.datetime,
) -> Outcome:
    row = (
        await db.execute(
            text(
                "UPDATE platform.approval_requests SET status = :s, completed_at = :now "  # noqa: S608
                f"WHERE id = :id RETURNING {_COLUMNS}"
            ),
            {"s": status, "now": now, "id": req.id},
        )
    ).one()
    done = Request(*row)
    await _skip_pending(db, req.id, None)
    handler = APPROVAL_HANDLERS.get(req.entity_type)
    callback = None
    if handler is not None:
        callback = {
            "approved": handler.on_approved,
            "rejected": handler.on_rejected,
            "cancelled": handler.on_cancelled,
        }[status]
    if callback is not None:
        await callback(
            db,
            ApprovalDecision(
                request_id=req.id,
                entity_type=req.entity_type,
                entity_id=req.entity_id,
                status=status,
                requested_by=req.requested_by,
                decided_by=decided_by,
                comment=comment,
                attributes=req.attributes,
            ),
        )
    await emit_event(
        db,
        aggregate_type="approval_request",
        aggregate_id=req.id,
        event_type=f"approval.{status}",
        payload={"entity_type": req.entity_type, "entity_id": str(req.entity_id)},
    )
    await audit.record(
        db,
        "approval.decided",
        "platform.approval_requests",
        req.id,
        {"status": status, "entity_type": req.entity_type},
    )
    emails: list[QueuedEmail] = []
    if req.requested_by is not None and req.requested_by != decided_by:
        verb = {"approved": "approved", "rejected": "rejected", "cancelled": "withdrawn"}[status]
        emails = await notify(
            db,
            kek,
            tenant_id=tenant_id,
            user_ids=[req.requested_by],
            type_key="approval.decided",
            title=f"{types.label(req.entity_type)} {verb}",
            link=f"/approvals/{req.id}",
            entity_type="approval_request",
            entity_id=req.id,
        )
    return Outcome(done, emails)


async def _visible(db: AsyncSession, req: Request, user_id: uuid.UUID) -> bool:
    if req.requested_by == user_id:
        return True
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM platform.approval_tasks "
                "WHERE approval_request_id = :r AND assignee_user_id = :u LIMIT 1"
            ),
            {"r": req.id, "u": user_id},
        )
    ).first()
    return row is not None


def _check_comment(comment: str | None, *, required: bool) -> str | None:
    comment = comment.strip() if comment else None
    if required and not comment:
        raise UnprocessableError("Say why.", code="comment_required")
    if comment and len(comment) > MAX_COMMENT_LENGTH:
        raise UnprocessableError(
            f"Comments are limited to {MAX_COMMENT_LENGTH} characters.", code="comment_too_long"
        )
    return comment


async def decide(
    db: AsyncSession,
    kek: KeyEncryptionKey,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    actor: uuid.UUID,
    approve: bool,
    comment: str | None,
    row_version: int,
    now: datetime.datetime | None = None,
) -> Outcome:
    """Record the actor's decision on their pending task and move the request along.

    Rejecting needs a comment. Anyone who isn't the requester or an assignee gets 404, and an
    assignee whose turn it isn't gets 403.
    """
    now = now or _utcnow()
    comment = _check_comment(comment, required=not approve)
    req = await get_request(db, request_id, lock=True)
    if not await _visible(db, req, actor):
        raise NotFoundError("Approval not found.")
    if req.status != "pending":
        raise ConflictError("This approval is already closed.", code="approval_closed")
    if req.row_version != row_version:
        raise ConflictError(
            "This approval changed. Reload and try again.", code="stale_row_version"
        )
    task = (
        await db.execute(
            text(
                "UPDATE platform.approval_tasks SET status = :s, acted_at = :now, comment = :c "
                "WHERE approval_request_id = :r AND step_no = :step AND assignee_user_id = :u "
                "  AND status = 'pending' RETURNING id"
            ),
            {
                "s": "approved" if approve else "rejected",
                "now": now,
                "c": comment,
                "r": req.id,
                "step": req.current_step,
                "u": actor,
            },
        )
    ).first()
    if task is None:
        raise ForbiddenError("This approval isn't waiting on you.", code="not_your_turn")
    if not approve:
        return await _finalize(
            db, kek, tenant_id, req, "rejected", decided_by=actor, comment=comment, now=now
        )
    spec = req.steps[req.current_step - 1]
    if spec.mode == "any":
        await _skip_pending(db, req.id, req.current_step)
    else:
        waiting: int = (
            await db.execute(
                text(
                    "SELECT count(*) FROM platform.approval_tasks "
                    "WHERE approval_request_id = :r AND step_no = :s AND status = 'pending'"
                ),
                {"r": req.id, "s": req.current_step},
            )
        ).scalar_one()
        if waiting:
            return Outcome(await get_request(db, req.id), [])
    if req.current_step < len(req.steps):
        row = (
            await db.execute(
                text(
                    "UPDATE platform.approval_requests SET current_step = current_step + 1 "  # noqa: S608
                    f"WHERE id = :id RETURNING {_COLUMNS}"
                ),
                {"id": req.id},
            )
        ).one()
        advanced = Request(*row)
        emails = await _open_step(db, kek, tenant_id, advanced, advanced.current_step, now)
        return Outcome(advanced, emails)
    return await _finalize(
        db, kek, tenant_id, req, "approved", decided_by=actor, comment=comment, now=now
    )


async def cancel(
    db: AsyncSession,
    kek: KeyEncryptionKey,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    actor: uuid.UUID,
    can_manage: bool,
    comment: str | None,
    row_version: int,
    now: datetime.datetime | None = None,
) -> Outcome:
    """Withdraw a pending request: its requester can, and so can anyone who manages
    approvals. Assignees who don't qualify get 403, everyone else 404."""
    now = now or _utcnow()
    comment = _check_comment(comment, required=False)
    req = await get_request(db, request_id, lock=True)
    is_requester = req.requested_by == actor
    if not (can_manage or is_requester or await _visible(db, req, actor)):
        raise NotFoundError("Approval not found.")
    if not (can_manage or is_requester):
        raise ForbiddenError("Only the requester can withdraw this.", code="forbidden")
    if req.status != "pending":
        raise ConflictError("This approval is already closed.", code="approval_closed")
    if req.row_version != row_version:
        raise ConflictError(
            "This approval changed. Reload and try again.", code="stale_row_version"
        )
    return await _finalize(
        db, kek, tenant_id, req, "cancelled", decided_by=actor, comment=comment, now=now
    )
