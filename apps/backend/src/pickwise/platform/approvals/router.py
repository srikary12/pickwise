# SPDX-License-Identifier: AGPL-3.0-only
"""Approvals API (/v1/approvals): the inbox, decisions, policies and delegations."""

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status

from pickwise.platform import audit
from pickwise.platform.approvals import delegations, policies, queries, service
from pickwise.platform.approvals.policy import StepSpec
from pickwise.platform.approvals.queries import InboxState
from pickwise.platform.approvals.schemas import (
    Decision,
    DelegationCreate,
    DelegationOut,
    InboxItem,
    InboxPage,
    PersonOut,
    PolicyCreate,
    PolicyOut,
    PolicyUpdate,
    RequestedItem,
    RequestedPage,
    RequestOut,
    StepOut,
    TaskOut,
)
from pickwise.platform.auth.dependencies import DB, Authorized, KekDep, require
from pickwise.platform.events import kick_relay
from pickwise.platform.idempotency import idempotent
from pickwise.platform.notifications.email import dispatch
from pickwise.shared.errors import ForbiddenError, UnprocessableError

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])

Act = Annotated[Authorized, Depends(require("platform.approvals.act"))]
Manage = Annotated[Authorized, Depends(require("platform.approvals.manage"))]
MANAGE = "platform.approvals.manage"


def _viewer(auth: Authorized) -> uuid.UUID | None:
    """The signed-in person, or None for an API key (which has no inbox of its own)."""
    return auth.principal.user_id


def _user(auth: Authorized) -> uuid.UUID:
    """The person acting. API keys read what their permissions allow but don't decide or
    delegate: those are acts of a person (and are attributed to one)."""
    if auth.principal.user_id is None:
        raise UnprocessableError(
            "Approvals are decided by people, not API keys.", code="people_only"
        )
    return auth.principal.user_id


def _policy_out(p: policies.PolicyRow) -> PolicyOut:
    return PolicyOut(
        id=p.id,
        key=p.key,
        entity_type=p.entity_type,
        name=p.name,
        priority=p.priority,
        conditions=p.conditions,
        steps=[StepSpec.model_validate(s) for s in p.steps],
        is_active=p.is_active,
        archived_at=p.archived_at,
        row_version=p.row_version,
    )


def _delegation_out(d: delegations.Delegation) -> DelegationOut:
    return DelegationOut(
        id=d.id,
        from_user_id=d.from_user_id,
        to_user_id=d.to_user_id,
        starts_at=d.starts_at,
        ends_at=d.ends_at,
        entity_types=d.entity_types,
    )


async def _request_out(
    db: DB, request_id: uuid.UUID, viewer: uuid.UUID | None, auth: Authorized
) -> RequestOut:
    can_manage = MANAGE in auth.grants
    req, requester, tasks = await queries.detail(db, request_id, viewer, can_view_all=can_manage)
    names: dict[uuid.UUID, str | None] = {t["assignee_user_id"]: t["assignee_name"] for t in tasks}
    if requester:
        names[requester["id"]] = requester["name"]

    def person(user_id: uuid.UUID) -> PersonOut:
        return PersonOut(id=user_id, name=names.get(user_id))

    steps: list[StepOut] = []
    my_task: uuid.UUID | None = None
    for number, spec in enumerate(req.steps, start=1):
        in_step = [t for t in tasks if t["step_no"] == number]
        if req.status == "pending" and number == req.current_step:
            state = "current"
            for t in in_step:
                if t["status"] == "pending" and t["assignee_user_id"] == viewer:
                    my_task = t["id"]
        elif number < req.current_step or (req.status != "pending" and in_step):
            state = "done"
        else:
            state = "upcoming"
        steps.append(
            StepOut(
                step_no=number,
                name=spec.name,
                mode=spec.mode,
                state=state,
                tasks=[
                    TaskOut(
                        id=t["id"],
                        assignee=person(t["assignee_user_id"]),
                        status=t["status"],
                        acted_at=t["acted_at"],
                        comment=t["comment"],
                        delegated_from=(
                            person(t["delegated_from_user_id"])
                            if t["delegated_from_user_id"]
                            else None
                        ),
                        due_at=t["due_at"],
                        created_at=t["created_at"],
                    )
                    for t in in_step
                ],
            )
        )
    return RequestOut(
        id=req.id,
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        status=req.status,
        current_step=req.current_step,
        policy_name=req.snapshot["name"],
        requested_by=person(req.requested_by) if req.requested_by else None,
        created_at=req.created_at,
        completed_at=req.completed_at,
        row_version=req.row_version,
        steps=steps,
        my_task_id=my_task,
        can_cancel=req.status == "pending"
        and viewer is not None
        and (can_manage or req.requested_by == viewer),
    )


# --- the caller's own work ------------------------------------------------------------------


@router.get("/inbox", response_model=InboxPage)
async def inbox(
    db: DB,
    auth: Act,
    inbox_state: Annotated[InboxState, Query(alias="state")] = "pending",
    before: Annotated[uuid.UUID | None, Query(description="Cursor: next_cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> InboxPage:
    """Tasks assigned to you. ``state=pending`` is what needs action, ``decided`` is what you
    already answered, ``all`` includes escalated and skipped ones."""
    viewer = _viewer(auth)
    if viewer is None:
        return InboxPage(items=[], next_cursor=None)
    items, cursor = await queries.inbox(db, viewer, state=inbox_state, before=before, limit=limit)
    return InboxPage(items=[InboxItem(**i) for i in items], next_cursor=cursor)


@router.get("/requested", response_model=RequestedPage)
async def requested(
    db: DB,
    auth: Act,
    before: Annotated[uuid.UUID | None, Query(description="Cursor: next_cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> RequestedPage:
    """Approvals you started."""
    viewer = _viewer(auth)
    if viewer is None:
        return RequestedPage(items=[], next_cursor=None)
    items, cursor = await queries.requested_by(db, viewer, before=before, limit=limit)
    return RequestedPage(items=[RequestedItem(**i) for i in items], next_cursor=cursor)


# --- policies -------------------------------------------------------------------------------


@router.get("/policies", response_model=list[PolicyOut])
async def list_policies(
    db: DB,
    auth: Manage,
    entity_type: str | None = None,
    include_archived: Annotated[bool, Query()] = False,
) -> list[PolicyOut]:
    rows = await policies.list_policies(
        db, entity_type=entity_type, include_archived=include_archived
    )
    return [_policy_out(p) for p in rows]


@router.post("/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
async def create_policy(
    body: PolicyCreate, request: Request, response: Response, db: DB, auth: Manage
) -> Any:
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    policy = await policies.create_policy(
        db,
        key=body.key,
        entity_type=body.entity_type,
        name=body.name,
        priority=body.priority,
        conditions=body.conditions,
        steps=[s.model_dump() for s in body.steps],
        is_active=body.is_active,
    )
    await audit.record(
        db,
        "approval_policy.created",
        "platform.approval_policies",
        policy.id,
        {"key": policy.key, "entity_type": policy.entity_type},
    )
    out = _policy_out(policy)
    await idem.finish(status.HTTP_201_CREATED, out.model_dump(mode="json"))
    return out


@router.put("/policies/{policy_id}", response_model=PolicyOut)
async def update_policy(
    policy_id: uuid.UUID, body: PolicyUpdate, db: DB, auth: Manage
) -> PolicyOut:
    """Edit a policy. Requests already in flight keep the policy they started with."""
    policy = await policies.update_policy(
        db,
        policy_id,
        name=body.name,
        priority=body.priority,
        conditions=body.conditions,
        steps=[s.model_dump() for s in body.steps],
        is_active=body.is_active,
        row_version=body.row_version,
    )
    await audit.record(db, "approval_policy.updated", "platform.approval_policies", policy.id)
    return _policy_out(policy)


@router.post("/policies/{policy_id}/archive", response_model=PolicyOut)
async def archive_policy(policy_id: uuid.UUID, db: DB, auth: Manage) -> PolicyOut:
    policy = await policies.set_archived(db, policy_id, True)
    await audit.record(db, "approval_policy.archived", "platform.approval_policies", policy.id)
    return _policy_out(policy)


@router.post("/policies/{policy_id}/unarchive", response_model=PolicyOut)
async def unarchive_policy(policy_id: uuid.UUID, db: DB, auth: Manage) -> PolicyOut:
    policy = await policies.set_archived(db, policy_id, False)
    await audit.record(db, "approval_policy.unarchived", "platform.approval_policies", policy.id)
    return _policy_out(policy)


# --- delegations ----------------------------------------------------------------------------


@router.get("/delegations", response_model=list[DelegationOut])
async def list_delegations(
    db: DB,
    auth: Act,
    everyone: Annotated[bool, Query(description="All delegations (needs manage).")] = False,
    include_ended: Annotated[bool, Query()] = False,
) -> list[DelegationOut]:
    if everyone and MANAGE not in auth.grants:
        raise ForbiddenError("You can only see your own delegations.", code="forbidden")
    viewer = _viewer(auth)
    if viewer is None and not everyone:
        return []
    rows = await delegations.list_delegations(
        db, user_id=None if everyone else viewer, include_ended=include_ended
    )
    return [_delegation_out(d) for d in rows]


@router.post("/delegations", response_model=DelegationOut, status_code=status.HTTP_201_CREATED)
async def create_delegation(
    body: DelegationCreate, request: Request, response: Response, db: DB, auth: Act
) -> Any:
    """Hand your approvals to someone for a period (leave, travel). While it is active,
    new tasks for you go to them and your own task is kept as ``delegated``."""
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    me = _user(auth)
    from_user = body.from_user_id or me
    if from_user != me and MANAGE not in auth.grants:
        raise ForbiddenError("You can only delegate your own approvals.", code="forbidden")
    for moment in (body.starts_at, body.ends_at):
        if moment.tzinfo is None:
            raise UnprocessableError(
                "Give the times with a time zone, e.g. 2026-10-05T09:00:00+05:30.",
                code="invalid_period",
            )
    delegation = await delegations.create_delegation(
        db,
        from_user=from_user,
        to_user=body.to_user_id,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        entity_types=list(body.entity_types),
    )
    await audit.record(
        db,
        "approval_delegation.created",
        "platform.delegations",
        delegation.id,
        {"from": str(from_user), "to": str(body.to_user_id)},
    )
    out = _delegation_out(delegation)
    await idem.finish(status.HTTP_201_CREATED, out.model_dump(mode="json"))
    return out


@router.post("/delegations/{delegation_id}/end", response_model=DelegationOut)
async def end_delegation(delegation_id: uuid.UUID, db: DB, auth: Act) -> DelegationOut:
    """Stop a delegation now. Yours, or anyone's with manage."""
    delegation = await delegations.end_delegation(
        db,
        delegation_id,
        only_for_user=None if MANAGE in auth.grants else _user(auth),
        now=datetime.datetime.now(datetime.UTC),
    )
    await audit.record(db, "approval_delegation.ended", "platform.delegations", delegation.id)
    return _delegation_out(delegation)


# --- one request ----------------------------------------------------------------------------


@router.get("/{request_id}", response_model=RequestOut)
async def get_request(request_id: uuid.UUID, db: DB, auth: Act) -> RequestOut:
    """A request with its steps and tasks. Visible to its requester, its assignees and
    people who manage approvals."""
    return await _request_out(db, request_id, _viewer(auth), auth)


async def _decide(
    request_id: uuid.UUID,
    body: Decision,
    db: DB,
    kek: KekDep,
    background: BackgroundTasks,
    auth: Authorized,
    *,
    approve: bool,
) -> RequestOut:
    outcome = await service.decide(
        db,
        kek,
        tenant_id=auth.tenant_id,
        request_id=request_id,
        actor=_user(auth),
        approve=approve,
        comment=body.comment,
        row_version=body.row_version,
    )
    background.add_task(dispatch, outcome.emails)
    background.add_task(kick_relay)
    return await _request_out(db, request_id, _user(auth), auth)


@router.post("/{request_id}/approve", response_model=RequestOut)
async def approve(
    request_id: uuid.UUID,
    body: Decision,
    db: DB,
    kek: KekDep,
    background: BackgroundTasks,
    auth: Act,
) -> RequestOut:
    """Approve your pending task. The request moves to the next step, or is approved."""
    return await _decide(request_id, body, db, kek, background, auth, approve=True)


@router.post("/{request_id}/reject", response_model=RequestOut)
async def reject(
    request_id: uuid.UUID,
    body: Decision,
    db: DB,
    kek: KekDep,
    background: BackgroundTasks,
    auth: Act,
) -> RequestOut:
    """Reject the request. A comment is required."""
    return await _decide(request_id, body, db, kek, background, auth, approve=False)


@router.post("/{request_id}/cancel", response_model=RequestOut)
async def cancel(
    request_id: uuid.UUID,
    body: Decision,
    db: DB,
    kek: KekDep,
    background: BackgroundTasks,
    auth: Act,
) -> RequestOut:
    """Withdraw a pending request (its requester, or anyone who manages approvals)."""
    outcome = await service.cancel(
        db,
        kek,
        tenant_id=auth.tenant_id,
        request_id=request_id,
        actor=_user(auth),
        can_manage=MANAGE in auth.grants,
        comment=body.comment,
        row_version=body.row_version,
    )
    background.add_task(dispatch, outcome.emails)
    background.add_task(kick_relay)
    return await _request_out(db, request_id, _user(auth), auth)
