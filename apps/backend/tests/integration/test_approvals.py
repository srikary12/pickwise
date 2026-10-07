# SPDX-License-Identifier: AGPL-3.0-only
"""Approval policies, steps, delegation, escalation and the decision API."""

import datetime
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.approvals import (
    APPROVAL_HANDLERS,
    APPROVER_RESOLVERS,
    ApprovalDecision,
    ApprovalHandler,
    ApproverUnresolvedError,
    NoApprovalPolicyError,
    Outcome,
    ResolveContext,
    deadlines,
    start_approval,
)
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database
from pickwise.shared.errors import ConflictError
from tests.integration.conftest import MakeApi
from tests.integration.support import Account, Api, Env, Tenant

pytestmark = pytest.mark.db

MANAGER_STEP = {"name": "Manager", "approvers": ["manager"]}
HR_STEP = {"name": "HR", "approvers": ["role:hr_admin"]}


@dataclass
class Org:
    """A small company: who reports to whom, and the accounts to act as."""

    tenant: Tenant
    env: Env
    db: Database
    make_api: MakeApi
    requester: Account
    boss: Account
    skip: Account
    hr1: Account
    hr2: Account
    deputy: Account
    admin_api: Api
    managers: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    decisions: list[ApprovalDecision] = field(default_factory=list)
    _apis: dict[uuid.UUID, Api] = field(default_factory=dict)

    async def api(self, account: Account) -> Api:
        if account.user_id not in self._apis:
            self._apis[account.user_id] = await (await self.make_api()).sign_in(account)
        return self._apis[account.user_id]

    async def policy(self, **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "key": f"p_{uuid.uuid4().hex[:8]}",
            "entity_type": "leave_request",
            "name": "Leave",
            "steps": [MANAGER_STEP],
            **overrides,
        }
        response = await self.admin_api.post("/v1/approvals/policies", body)
        assert response.status_code == 201, response.text
        return dict(response.json())

    async def start(
        self,
        *,
        entity_type: str = "leave_request",
        attributes: dict[str, Any] | None = None,
        requested_by: Account | None = None,
        entity_id: uuid.UUID | None = None,
        now: datetime.datetime | None = None,
    ) -> Outcome:
        who = requested_by or self.requester
        ctx = RequestContext(ActorType.USER, self.tenant.id, who.user_id)
        async with self.db.tenant_session(ctx) as session:
            outcome = await start_approval(
                session,
                self.env.kek,
                tenant_id=self.tenant.id,
                entity_type=entity_type,
                entity_id=entity_id or uuid.uuid4(),
                attributes=attributes if attributes is not None else {"days": 3},
                requested_by=who.user_id,
                now=now,
            )
        for email in outcome.emails:
            await self.env.mailbox.dispatcher(email)
        return outcome

    async def detail(self, account: Account, request_id: uuid.UUID) -> dict[str, Any]:
        response = await (await self.api(account)).get(f"/v1/approvals/{request_id}")
        assert response.status_code == 200, response.text
        return dict(response.json())

    async def act(
        self,
        account: Account,
        request_id: uuid.UUID,
        verb: str = "approve",
        *,
        comment: str | None = None,
        row_version: int | None = None,
        expect: int = 200,
    ) -> dict[str, Any]:
        api = await self.api(account)
        if row_version is None:
            row_version = (await self.detail(account, request_id))["row_version"]
        response = await api.post(
            f"/v1/approvals/{request_id}/{verb}", {"comment": comment, "row_version": row_version}
        )
        assert response.status_code == expect, response.text
        return dict(response.json())

    async def inbox(self, account: Account, state: str = "pending") -> list[dict[str, Any]]:
        response = await (await self.api(account)).get(f"/v1/approvals/inbox?state={state}")
        assert response.status_code == 200, response.text
        return list(response.json()["items"])

    async def events(self, event_type: str) -> int:
        rows = await self.env.sql(
            "SELECT count(*) FROM platform.outbox_events WHERE tenant_id = :t AND event_type = :e",
            t=self.tenant.id,
            e=event_type,
        )
        return int(rows[0][0])

    async def notifications(self, account: Account, type_key: str) -> int:
        rows = await self.env.sql(
            "SELECT count(*) FROM platform.notifications WHERE tenant_id = :t AND user_id = :u "
            "AND type = :ty",
            t=self.tenant.id,
            u=account.user_id,
            ty=type_key,
        )
        return int(rows[0][0])

    async def run_deadlines(self, now: datetime.datetime) -> None:
        """The worker's minutely job, with the clock set. It handles every tenant in the
        database, so tests assert on their own tenant's rows only."""
        for email in await deadlines.run_deadlines(self.env.db, self.env.kek, now):
            await self.env.mailbox.dispatcher(email)

    async def reminded(self) -> int:
        rows = await self.env.sql(
            "SELECT count(*) FROM platform.approval_tasks "
            "WHERE tenant_id = :t AND reminded_at IS NOT NULL",
            t=self.tenant.id,
        )
        return int(rows[0][0])


@pytest.fixture
async def org(env: Env, tenant: Tenant, api_db: Database, make_api: MakeApi) -> AsyncIterator[Org]:
    accounts: dict[str, Account] = {
        name: await env.add_account(tenant, role)
        for name, role in (
            ("requester", "employee"),
            ("boss", "manager"),
            ("skip", "manager"),
            ("hr1", "hr_admin"),
            ("hr2", "hr_admin"),
            ("deputy", "employee"),
        )
    }
    admin_api = await (await make_api()).sign_in(tenant.admin)
    organisation = Org(
        tenant,
        env,
        api_db,
        make_api,
        requester=accounts["requester"],
        boss=accounts["boss"],
        skip=accounts["skip"],
        hr1=accounts["hr1"],
        hr2=accounts["hr2"],
        deputy=accounts["deputy"],
        admin_api=admin_api,
    )
    organisation.managers[accounts["requester"].user_id] = accounts["boss"].user_id
    organisation.managers[accounts["boss"].user_id] = accounts["skip"].user_id

    async def manager(session: AsyncSession, ctx: ResolveContext) -> list[uuid.UUID]:
        who = ctx.requested_by
        found = organisation.managers.get(who) if who else None
        return [found] if found else []

    async def skip_level(session: AsyncSession, ctx: ResolveContext) -> list[uuid.UUID]:
        who = ctx.escalating_from or (
            organisation.managers.get(ctx.requested_by) if ctx.requested_by else None
        )
        found = organisation.managers.get(who) if who else None
        return [found] if found else []

    async def approved(session: AsyncSession, decision: ApprovalDecision) -> None:
        organisation.decisions.append(decision)

    # Core registers the real resolvers; these tests use a table of who reports to whom.
    real = {kind: APPROVER_RESOLVERS.get(kind) for kind in ("manager", "skip_level")}
    for kind in real:
        APPROVER_RESOLVERS.unregister(kind)
    APPROVER_RESOLVERS.register("manager", manager)
    APPROVER_RESOLVERS.register("skip_level", skip_level)
    handler = ApprovalHandler(approved, approved, approved)
    for entity_type in ("leave_request", "offer"):
        APPROVAL_HANDLERS.register(entity_type, handler)
    yield organisation
    for kind, resolver in real.items():
        APPROVER_RESOLVERS.unregister(kind)
        if resolver is not None:
            APPROVER_RESOLVERS.register(kind, resolver)
    for entity_type in ("leave_request", "offer"):
        APPROVAL_HANDLERS.unregister(entity_type)


# --- policies --------------------------------------------------------------------------------


async def test_policy_crud_validation_and_permissions(org: Org) -> None:
    created = await org.policy(
        key="long_leave",
        priority=10,
        conditions={"field": "days", "op": "gte", "value": 5},
        steps=[{**MANAGER_STEP, "escalate_after_hours": 48}, {**HR_STEP, "mode": "all"}],
    )
    assert created["steps"][0]["escalate_after_hours"] == 48
    assert created["steps"][1]["mode"] == "all"
    assert created["row_version"] == 1

    # Invalid documents are refused before anything is stored.
    api = org.admin_api
    invalid: list[dict[str, Any]] = [
        {"conditions": {"field": "days", "op": "regex", "value": "x"}},
        {"conditions": {"eval": "1"}},
        {"steps": []},
        {"steps": [{"name": "x", "approvers": ["boss"]}]},
        {"steps": [{"name": "x", "approvers": ["manager"], "mode": "majority"}]},
        {"entity_type": "timesheet"},
        {"key": "Has Spaces"},
    ]
    for bad in invalid:
        body = {"key": "k", "entity_type": "leave_request", "name": "n", "steps": [MANAGER_STEP]}
        response = await api.post("/v1/approvals/policies", {**body, **bad})
        assert response.status_code == 422, bad
    duplicate = await api.post(
        "/v1/approvals/policies",
        {"key": "long_leave", "entity_type": "leave_request", "name": "n", "steps": [MANAGER_STEP]},
    )
    assert duplicate.status_code == 409

    updated = await api.put(
        f"/v1/approvals/policies/{created['id']}",
        {
            "name": "Long leave",
            "priority": 20,
            "conditions": {},
            "steps": [MANAGER_STEP],
            "is_active": True,
            "row_version": 1,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["row_version"] == 2
    stale = await api.put(
        f"/v1/approvals/policies/{created['id']}",
        {"name": "x", "steps": [MANAGER_STEP], "row_version": 1},
    )
    assert stale.status_code == 409

    assert (await api.post(f"/v1/approvals/policies/{created['id']}/archive")).json()["archived_at"]
    listing = (await api.get("/v1/approvals/policies")).json()
    assert created["id"] not in [p["id"] for p in listing]
    assert created["id"] in [
        p["id"] for p in (await api.get("/v1/approvals/policies?include_archived=true")).json()
    ]
    unarchived = await api.post(f"/v1/approvals/policies/{created['id']}/unarchive")
    assert unarchived.json()["archived_at"] is None

    employee = await org.api(org.requester)
    assert (await employee.get("/v1/approvals/policies")).status_code == 403
    assert (await employee.post("/v1/approvals/policies", {"key": "x"})).status_code == 403


async def test_the_highest_priority_matching_policy_wins_and_is_snapshotted(org: Org) -> None:
    await org.policy(key="fallback", priority=0, steps=[MANAGER_STEP])
    long_leave = await org.policy(
        key="long",
        priority=10,
        conditions={"field": "days", "op": "gte", "value": 5},
        steps=[HR_STEP],
    )
    short = await org.start(attributes={"days": 2})
    long = await org.start(attributes={"days": 7})
    assert short.request.snapshot["key"] == "fallback"
    assert long.request.snapshot["key"] == "long"
    assert long.request.policy_id == uuid.UUID(long_leave["id"])
    assert long.request.attributes == {"days": 7}

    # Editing the policy later doesn't touch the request in flight.
    await org.admin_api.put(
        f"/v1/approvals/policies/{long_leave['id']}",
        {
            "name": "Changed",
            "priority": 10,
            "conditions": {},
            "steps": [MANAGER_STEP, HR_STEP],
            "is_active": True,
            "row_version": long_leave["row_version"],
        },
    )
    detail = await org.detail(org.requester, long.request.id)
    assert detail["policy_name"] == "Leave"
    assert [s["name"] for s in detail["steps"]] == ["HR"]


async def test_nothing_starts_without_a_matching_policy(org: Org) -> None:
    with pytest.raises(NoApprovalPolicyError):
        await org.start()
    await org.policy(conditions={"field": "days", "op": "gte", "value": 5})
    with pytest.raises(NoApprovalPolicyError):
        await org.start(attributes={"days": 1})
    # An inactive or archived policy doesn't count either.
    inactive = await org.policy(
        key="off", is_active=False, conditions={"field": "days", "op": "eq", "value": 1}
    )
    with pytest.raises(NoApprovalPolicyError):
        await org.start(attributes={"days": 1})
    assert inactive["is_active"] is False


async def test_a_step_nobody_can_approve_fails_closed_and_leaves_nothing_behind(org: Org) -> None:
    await org.policy(steps=[MANAGER_STEP])
    stranger = await org.env.add_account(org.tenant, "employee")  # has no manager
    with pytest.raises(ApproverUnresolvedError):
        await org.start(requested_by=stranger)
    # A kind nobody registered resolves to nobody.
    APPROVER_RESOLVERS.unregister("manager")
    with pytest.raises(ApproverUnresolvedError):
        await org.start()
    rows = await org.env.sql(
        "SELECT count(*) FROM platform.approval_requests WHERE tenant_id = :t", t=org.tenant.id
    )
    assert rows == [(0,)]


async def test_the_requester_cannot_approve_their_own_request_unless_the_step_allows_it(
    org: Org,
) -> None:
    await org.policy(key="strict", entity_type="offer", steps=[HR_STEP])
    # An HR admin asks for approval from the HR role: they're left out of their own request.
    outcome = await org.start(entity_type="offer", requested_by=org.hr1)
    tasks = (await org.detail(org.hr1, outcome.request.id))["steps"][0]["tasks"]
    assert {t["assignee"]["id"] for t in tasks} == {str(org.hr2.user_id)}

    # A tenant whose only holder of the role is the requester: nobody else can approve, so it
    # fails closed ...
    only_admin = await org.env.create_tenant()
    api = await (await org.make_api()).sign_in(only_admin.admin)
    created = await api.post(
        "/v1/approvals/policies",
        {
            "key": "admin_only",
            "entity_type": "offer",
            "name": "Offer",
            "steps": [{"name": "Admin", "approvers": ["role:tenant_admin"]}],
        },
    )
    assert created.status_code == 201
    ctx = RequestContext(ActorType.USER, only_admin.id, only_admin.admin.user_id)
    async with org.db.tenant_session(ctx) as session:
        with pytest.raises(ApproverUnresolvedError):
            await start_approval(
                session,
                org.env.kek,
                tenant_id=only_admin.id,
                entity_type="offer",
                entity_id=uuid.uuid4(),
                attributes={},
                requested_by=only_admin.admin.user_id,
            )
    # ... unless the policy lets the requester approve.
    updated = await api.put(
        f"/v1/approvals/policies/{created.json()['id']}",
        {
            "name": "Offer",
            "steps": [
                {"name": "Admin", "approvers": ["role:tenant_admin"], "allow_self_approval": True}
            ],
            "row_version": 1,
        },
    )
    assert updated.status_code == 200
    async with org.db.tenant_session(ctx) as session:
        outcome = await start_approval(
            session,
            org.env.kek,
            tenant_id=only_admin.id,
            entity_type="offer",
            entity_id=uuid.uuid4(),
            attributes={},
            requested_by=only_admin.admin.user_id,
        )
    assert outcome.request.status == "pending"


async def test_only_one_pending_approval_per_entity(org: Org) -> None:
    await org.policy()
    entity = uuid.uuid4()
    first = await org.start(entity_id=entity)
    with pytest.raises(ConflictError, match="already has a pending approval"):
        await org.start(entity_id=entity)
    await org.act(org.boss, first.request.id)  # approved: the entity is free again
    assert (await org.start(entity_id=entity)).request.status == "pending"


# --- steps and decisions ---------------------------------------------------------------------


async def test_two_steps_in_order_with_an_all_of_step(org: Org) -> None:
    await org.policy(steps=[MANAGER_STEP, {**HR_STEP, "mode": "all"}])
    started = await org.start()
    rid = started.request.id

    # Step 1 only reaches the manager; HR hears nothing yet.
    assert [i["request_id"] for i in await org.inbox(org.boss)] == [str(rid)]
    assert await org.inbox(org.hr1) == []
    assert await org.notifications(org.boss, "approval.assigned") == 1
    assert await org.notifications(org.hr1, "approval.assigned") == 0
    # Someone who isn't a party can't even tell it exists; a bystander in the tenant gets 404.
    other = await org.env.add_account(org.tenant, "employee")
    assert (await (await org.api(other)).get(f"/v1/approvals/{rid}")).status_code == 404

    after_boss = await org.act(org.boss, rid, comment="Fine by me")
    assert after_boss["status"] == "pending"
    assert after_boss["current_step"] == 2
    assert after_boss["my_task_id"] is None
    assert [s["state"] for s in after_boss["steps"]] == ["done", "current"]
    # Step 2 went to both HR admins; both must approve.
    assert {i["request_id"] for i in await org.inbox(org.hr1)} == {str(rid)}
    assert {i["request_id"] for i in await org.inbox(org.hr2)} == {str(rid)}

    still = await org.act(org.hr1, rid)
    assert still["status"] == "pending"
    assert still["current_step"] == 2
    assert org.decisions == []  # nothing final yet
    done = await org.act(org.hr2, rid)
    assert done["status"] == "approved"
    assert done["completed_at"]

    (decision,) = org.decisions
    assert (decision.status, decision.entity_type) == ("approved", "leave_request")
    assert decision.decided_by == org.hr2.user_id
    assert decision.requested_by == org.requester.user_id
    # The requester is told, an event is published, and the audit trail has it.
    assert await org.notifications(org.requester, "approval.decided") == 1
    assert await org.events("approval.requested") >= 1
    assert await org.events("approval.approved") == 1
    audited = await org.env.sql(
        "SELECT count(*) FROM audit.events WHERE tenant_id = :t AND action = 'approval.decided' "
        "AND entity_id = :e",
        t=org.tenant.id,
        e=rid,
    )
    assert audited == [(1,)]
    assert await org.inbox(org.hr1) == []
    decided = await org.inbox(org.hr1, "decided")
    assert [d["task_status"] for d in decided] == ["approved"]


async def test_an_any_of_step_is_decided_by_the_first_approver(org: Org) -> None:
    await org.policy(steps=[{**HR_STEP, "mode": "any"}])
    rid = (await org.start()).request.id
    assert (await org.act(org.hr2, rid))["status"] == "approved"
    tasks = (await org.detail(org.requester, rid))["steps"][0]["tasks"]
    assert sorted(t["status"] for t in tasks) == ["approved", "skipped"]
    # The other approver's turn is over: the request is closed.
    await org.act(org.hr1, rid, expect=409)


async def test_rejecting_needs_a_comment_and_ends_the_request(org: Org) -> None:
    await org.policy(steps=[MANAGER_STEP, HR_STEP])
    rid = (await org.start()).request.id
    await org.act(org.boss, rid, "reject", expect=422)
    rejected = await org.act(org.boss, rid, "reject", comment="Team is short-staffed")
    assert rejected["status"] == "rejected"
    assert rejected["steps"][0]["tasks"][0]["comment"] == "Team is short-staffed"
    assert [s["state"] for s in rejected["steps"]] == ["done", "upcoming"]
    assert org.decisions[-1].status == "rejected"
    assert org.decisions[-1].comment == "Team is short-staffed"
    assert await org.inbox(org.hr1) == []  # step 2 never opened
    assert await org.notifications(org.requester, "approval.decided") == 1
    await org.act(org.boss, rid, "approve", expect=409)


async def test_rejecting_during_an_all_of_step_closes_it_for_everyone(org: Org) -> None:
    await org.policy(steps=[{**HR_STEP, "mode": "all"}])
    rid = (await org.start()).request.id
    await org.act(org.hr1, rid)
    await org.act(org.hr2, rid, "reject", comment="No")
    detail = await org.detail(org.requester, rid)
    assert detail["status"] == "rejected"
    assert sorted(t["status"] for t in detail["steps"][0]["tasks"]) == ["approved", "rejected"]


async def test_decision_guards(org: Org) -> None:
    await org.policy(steps=[MANAGER_STEP, HR_STEP])
    rid = (await org.start()).request.id
    version = (await org.detail(org.boss, rid))["row_version"]

    # Not waiting on you (visible as the requester, but it's not your turn): 403.
    await org.act(org.requester, rid, expect=403, row_version=version)
    # Step 2's approver can't jump the queue; they can't see the request yet either.
    await org.act(org.hr1, rid, expect=404, row_version=version)
    # A stale version is a conflict, and a decision changes the version.
    await org.act(org.boss, rid, row_version=version + 5, expect=409)
    await org.act(org.boss, rid, row_version=version)
    await org.act(org.boss, rid, expect=403)  # their task is already acted on
    # The version is required.
    missing = await (await org.api(org.boss)).post(f"/v1/approvals/{rid}/approve", {})
    assert missing.status_code == 422


async def test_requesters_and_managers_can_cancel_but_not_assignees(org: Org) -> None:
    await org.policy()
    first = (await org.start()).request.id
    await org.act(org.boss, first, "cancel", expect=403)  # assignee, not requester
    cancelled = await org.act(org.requester, first, "cancel", comment="Plans changed")
    assert cancelled["status"] == "cancelled"
    assert org.decisions[-1].status == "cancelled"
    assert await org.inbox(org.boss) == []  # their task was skipped
    await org.act(org.requester, first, "cancel", expect=409)

    second = (await org.start()).request.id
    # HR admins hold platform.approvals.manage and may withdraw anyone's request.
    withdrawn = await org.act(org.hr1, second, "cancel")
    assert withdrawn["status"] == "cancelled"
    assert await org.notifications(org.requester, "approval.decided") == 1  # for the second


async def test_the_requested_list_and_inbox_page(org: Org) -> None:
    await org.policy()
    ids = [(await org.start()).request.id for _ in range(3)]
    api = await org.api(org.requester)
    page = (await api.get("/v1/approvals/requested?limit=2")).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"]
    rest = (await api.get(f"/v1/approvals/requested?before={page['next_cursor']}")).json()
    assert len(rest["items"]) == 1
    assert {i["request_id"] for i in page["items"] + rest["items"]} == {str(i) for i in ids}
    boss = await org.api(org.boss)
    inbox = (await boss.get("/v1/approvals/inbox?limit=2")).json()
    assert len(inbox["items"]) == 2
    assert inbox["items"][0]["requester_name"]
    assert inbox["items"][0]["step_name"] == "Manager"
    assert inbox["items"][0]["policy_name"] == "Leave"
    assert (await boss.get(f"/v1/approvals/inbox?before={inbox['next_cursor']}")).json()["items"]


async def test_another_tenant_sees_nothing(org: Org, env: Env) -> None:
    await org.policy()
    rid = (await org.start()).request.id
    other = await env.create_tenant()
    other_admin = await (await org.make_api()).sign_in(other.admin)
    assert (await other_admin.get(f"/v1/approvals/{rid}")).status_code == 404
    assert (await other_admin.get("/v1/approvals/policies")).json() == []
    assert (await other_admin.get("/v1/approvals/inbox")).json()["items"] == []


# --- delegation ------------------------------------------------------------------------------


def window(days_ahead: int = 7, *, start_hours_ago: int = 1) -> dict[str, str]:
    now = datetime.datetime.now(datetime.UTC)
    return {
        "starts_at": (now - datetime.timedelta(hours=start_hours_ago)).isoformat(),
        "ends_at": (now + datetime.timedelta(days=days_ahead)).isoformat(),
    }


async def test_delegation_redirects_new_tasks_and_keeps_the_record(org: Org) -> None:
    await org.policy()
    boss_api = await org.api(org.boss)
    created = await boss_api.post(
        "/v1/approvals/delegations", {"to_user_id": str(org.deputy.user_id), **window()}
    )
    assert created.status_code == 201, created.text
    rid = (await org.start()).request.id

    tasks = (await org.detail(org.requester, rid))["steps"][0]["tasks"]
    assert sorted(t["status"] for t in tasks) == ["delegated", "pending"]
    working = next(t for t in tasks if t["status"] == "pending")
    assert working["assignee"]["id"] == str(org.deputy.user_id)
    assert working["delegated_from"]["id"] == str(org.boss.user_id)
    assert await org.inbox(org.boss) == []
    assert [i["delegated_from_user_id"] for i in await org.inbox(org.deputy)] == [
        str(org.boss.user_id)
    ]
    # The delegator can see the request (their delegated task) but can't decide it.
    await org.act(org.boss, rid, expect=403)
    assert (await org.act(org.deputy, rid))["status"] == "approved"


async def test_delegation_respects_its_window_and_request_types(org: Org) -> None:
    await org.policy()
    await org.policy(key="offer_p", entity_type="offer")
    boss_api = await org.api(org.boss)
    # Only for offers: a leave request still goes to the boss.
    only_offers = await boss_api.post(
        "/v1/approvals/delegations",
        {"to_user_id": str(org.deputy.user_id), "entity_types": ["offer"], **window()},
    )
    assert only_offers.status_code == 201
    leave = (await org.start()).request.id
    assert len(await org.inbox(org.boss)) == 1
    await org.act(org.boss, leave)

    # A delegation that hasn't started, or has ended, does nothing.
    future = await boss_api.post(
        "/v1/approvals/delegations",
        {"to_user_id": str(org.deputy.user_id), **window(8, start_hours_ago=-24)},
    )
    assert future.status_code == 201
    await org.start()
    assert len(await org.inbox(org.boss)) == 1
    assert await org.inbox(org.deputy) == []
    # Ending a running delegation takes effect at once; one that hasn't started is removed.
    running = await boss_api.post(
        "/v1/approvals/delegations", {"to_user_id": str(org.deputy.user_id), **window()}
    )
    ended = await boss_api.post(f"/v1/approvals/delegations/{running.json()['id']}/end")
    assert ended.status_code == 200
    removed = await boss_api.post(f"/v1/approvals/delegations/{future.json()['id']}/end")
    assert removed.status_code == 200
    await org.start()
    assert len(await org.inbox(org.boss)) == 2
    mine = (await boss_api.get("/v1/approvals/delegations")).json()
    assert [d["id"] for d in mine] == [only_offers.json()["id"]]


async def test_delegation_rules(org: Org) -> None:
    boss_api = await org.api(org.boss)
    me = str(org.boss.user_id)
    bad = [
        {"to_user_id": me, **window()},  # to yourself
        {"to_user_id": str(uuid.uuid4()), **window()},  # not a member
        {"to_user_id": str(org.deputy.user_id), **window(-1)},  # ends before it starts
        {"to_user_id": str(org.deputy.user_id), **window(400)},  # too long
        {
            "to_user_id": str(org.deputy.user_id),
            "starts_at": "2026-01-01T00:00:00",
            "ends_at": "2026-02-01T00:00:00",
        },  # no time zone
        {"to_user_id": str(org.deputy.user_id), "entity_types": ["timesheet"], **window()},
    ]
    for body in bad:
        assert (await boss_api.post("/v1/approvals/delegations", body)).status_code == 422, body
    # Delegating someone else's approvals needs manage.
    on_behalf = {
        "from_user_id": str(org.hr1.user_id),
        "to_user_id": str(org.deputy.user_id),
        **window(),
    }
    assert (await boss_api.post("/v1/approvals/delegations", on_behalf)).status_code == 403
    admin_made = await org.admin_api.post("/v1/approvals/delegations", on_behalf)
    assert admin_made.status_code == 201
    # Others' delegations are invisible and can't be ended without manage.
    employee = await org.api(org.requester)
    assert (await employee.get("/v1/approvals/delegations")).json() == []
    assert (await employee.get("/v1/approvals/delegations?everyone=true")).status_code == 403
    assert (
        await employee.post(f"/v1/approvals/delegations/{admin_made.json()['id']}/end")
    ).status_code == 404
    everyone = await org.admin_api.get("/v1/approvals/delegations?everyone=true")
    assert [d["id"] for d in everyone.json()] == [admin_made.json()["id"]]


# --- reminders and escalation ----------------------------------------------------------------


async def test_reminder_at_half_the_window_then_escalation_to_the_skip_level(org: Org) -> None:
    await org.policy(steps=[{**MANAGER_STEP, "escalate_after_hours": 24}, HR_STEP])
    t0 = datetime.datetime.now(datetime.UTC)
    rid = (await org.start(now=t0)).request.id

    # Nothing is due yet.
    await org.run_deadlines(t0 + datetime.timedelta(hours=6))
    assert await org.reminded() == 0
    assert await org.notifications(org.boss, "approval.reminder") == 0
    # Half way: one reminder, once.
    await org.run_deadlines(t0 + datetime.timedelta(hours=13))
    assert await org.reminded() == 1
    assert await org.notifications(org.boss, "approval.reminder") == 1
    await org.run_deadlines(t0 + datetime.timedelta(hours=14))
    assert await org.notifications(org.boss, "approval.reminder") == 1

    # Overdue: the boss's task is escalated to the boss's manager, who is told.
    await org.run_deadlines(t0 + datetime.timedelta(hours=25))
    tasks = (await org.detail(org.requester, rid))["steps"][0]["tasks"]
    assert {(t["assignee"]["id"], t["status"]) for t in tasks} == {
        (str(org.boss.user_id), "escalated"),
        (str(org.skip.user_id), "pending"),
    }
    assert await org.notifications(org.skip, "approval.escalated") == 1
    assert await org.notifications(org.boss, "approval.escalated") == 1
    assert await org.events("approval.escalated") == 1
    assert await org.inbox(org.boss) == []
    assert len(await org.inbox(org.skip)) == 1
    # It happens once: the fallback's task has no deadline.
    await org.run_deadlines(t0 + datetime.timedelta(days=30))
    assert await org.events("approval.escalated") == 1
    # The original approver can no longer act; the fallback can, and the flow continues.
    await org.act(org.boss, rid, expect=403)
    assert (await org.act(org.skip, rid))["current_step"] == 2
    assert (await org.act(org.hr1, rid))["status"] == "approved"


async def test_escalation_falls_back_to_the_tenant_admin_role(org: Org) -> None:
    APPROVER_RESOLVERS.unregister("skip_level")
    await org.policy(steps=[{**MANAGER_STEP, "escalate_after_hours": 4}])
    t0 = datetime.datetime.now(datetime.UTC)
    rid = (await org.start(now=t0)).request.id
    await org.run_deadlines(t0 + datetime.timedelta(hours=5))
    pending = [
        t
        for t in (await org.detail(org.requester, rid))["steps"][0]["tasks"]
        if t["status"] == "pending"
    ]
    assert [t["assignee"]["id"] for t in pending] == [str(org.tenant.admin.user_id)]


async def test_a_step_can_name_its_own_fallback(org: Org) -> None:
    await org.policy(
        steps=[
            {**MANAGER_STEP, "escalate_after_hours": 4, "fallback": f"user:{org.deputy.user_id}"}
        ]
    )
    t0 = datetime.datetime.now(datetime.UTC)
    rid = (await org.start(now=t0)).request.id
    await org.run_deadlines(t0 + datetime.timedelta(hours=5))
    assert [i["request_id"] for i in await org.inbox(org.deputy)] == [str(rid)]
    assert (await org.act(org.deputy, rid))["status"] == "approved"


async def test_an_escalation_with_nobody_to_hand_to_leaves_the_task_alone(org: Org) -> None:
    APPROVER_RESOLVERS.unregister("skip_level")
    await org.policy(steps=[{**MANAGER_STEP, "escalate_after_hours": 4, "fallback": "dept_head"}])
    t0 = datetime.datetime.now(datetime.UTC)
    rid = (await org.start(now=t0)).request.id
    await org.run_deadlines(t0 + datetime.timedelta(hours=5))
    detail = await org.detail(org.requester, rid)
    assert detail["status"] == "pending"  # an approval never passes by itself
    assert [t["status"] for t in detail["steps"][0]["tasks"]] == ["pending"]
    assert detail["steps"][0]["tasks"][0]["due_at"] is None  # and stops trying
    assert len(await org.inbox(org.boss)) == 1


# --- the whole story -------------------------------------------------------------------------


async def test_two_steps_with_delegation_and_escalation(org: Org) -> None:
    """Done-when check: a 2-step approval where the first approver is away (delegated), the
    delegate doesn't answer in time (escalated), and the flow still completes."""
    await org.policy(
        conditions={"field": "days", "op": "gte", "value": 3},
        steps=[{**MANAGER_STEP, "escalate_after_hours": 24}, HR_STEP],
    )
    boss_api = await org.api(org.boss)
    assert (
        await boss_api.post(
            "/v1/approvals/delegations", {"to_user_id": str(org.deputy.user_id), **window()}
        )
    ).status_code == 201
    t0 = datetime.datetime.now(datetime.UTC)
    rid = (await org.start(attributes={"days": 4}, now=t0)).request.id

    assert [i["delegated_from_user_id"] for i in await org.inbox(org.deputy)] == [
        str(org.boss.user_id)
    ]
    # The deputy sits on it past the deadline: it escalates from the deputy to *their* manager.
    org.managers[org.deputy.user_id] = org.skip.user_id
    await org.run_deadlines(t0 + datetime.timedelta(hours=25))
    statuses = {
        (t["assignee"]["id"], t["status"])
        for t in (await org.detail(org.requester, rid))["steps"][0]["tasks"]
    }
    assert statuses == {
        (str(org.boss.user_id), "delegated"),
        (str(org.deputy.user_id), "escalated"),
        (str(org.skip.user_id), "pending"),
    }
    assert (await org.act(org.skip, rid))["current_step"] == 2
    final = await org.act(org.hr2, rid, comment="Approved")
    assert final["status"] == "approved"
    assert [d.status for d in org.decisions] == ["approved"]
    assert await org.events("approval.approved") == 1


# --- the dashboard summary -------------------------------------------------------------------


async def test_dashboard_summarises_my_waiting_work(org: Org) -> None:
    await org.policy(steps=[HR_STEP])
    for _ in range(7):
        await org.start()

    hr = await (await org.api(org.hr1)).get("/v1/dashboard")
    assert hr.status_code == 200, hr.text
    body = hr.json()
    assert body["pending_approvals"]["count"] == 7
    assert len(body["pending_approvals"]["items"]) == 5  # the page shows five, the badge all
    assert body["pending_approvals"]["items"][0]["task_status"] == "pending"
    (unread,) = (
        await org.env.sql(
            "SELECT count(*) FROM platform.notifications "
            "WHERE user_id = :u AND read_at IS NULL AND tenant_id = :t",
            u=org.hr1.user_id,
            t=org.tenant.id,
        )
    )[0]
    assert body["notifications"]["unread"] == unread
    assert len(body["notifications"]["items"]) == min(5, unread)

    # Strictly the caller's own: someone not asked sees nothing.
    other = (await (await org.api(org.deputy)).get("/v1/dashboard")).json()
    assert other["pending_approvals"] == {"count": 0, "items": []}


async def test_dashboard_needs_a_session(org: Org) -> None:
    anonymous = await org.make_api()
    assert (await anonymous.get("/v1/dashboard")).status_code == 401
