# SPDX-License-Identifier: AGPL-3.0-only
"""Employees: creation, onboarding status, job history and the reporting hierarchy."""

import datetime
import itertools

import pytest

from pickwise.core import hierarchy
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database
from tests.integration.conftest import MakeApi
from tests.integration.core_support import Org, link, make_employee, make_org, today
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db


async def hr(make_api: MakeApi, tenant: Tenant) -> tuple[Api, Org]:
    api = await (await make_api()).sign_in(tenant.admin)
    return api, await make_org(api)


async def closure(env: Env, tenant: Tenant) -> set[tuple[str, str, int]]:
    rows = await env.sql(
        "SELECT ancestor_employee_id::text, descendant_employee_id::text, depth "
        "FROM core.employee_hierarchy WHERE tenant_id = :t",
        t=tenant.id,
    )
    return {(str(r[0]), str(r[1]), int(r[2])) for r in rows}


# --- creating --------------------------------------------------------------------------------


async def test_create_employee_with_first_job_record(make_api: MakeApi, tenant: Tenant) -> None:
    api, org = await hr(make_api, tenant)
    first = await make_employee(api, org, "Asha", activate=False)
    second = await make_employee(api, org, "Ravi", activate=False)
    assert first["employee_code"] == "EMP00001"
    assert second["employee_code"] == "EMP00002"
    assert first["status"] == "draft"
    assert first["display_name"] == "Asha Tester"
    assert first["job"]["department_name"] == "ENG"
    assert first["job"]["manager_employee_id"] is None
    assert first["has_login"] is False

    records = (await api.get(f"/v1/employees/{first['id']}/job-records")).json()
    assert len(records) == 1
    assert records[0]["change_reason"] == "hire"
    assert records[0]["is_current"] is True
    assert records[0]["valid_to"] is None


async def test_create_is_idempotent_and_validates(make_api: MakeApi, tenant: Tenant) -> None:
    api, org = await hr(make_api, tenant)
    body = {
        "first_name": "Meera",
        "date_of_joining": today().isoformat(),
        "work_email": "meera@example.test",
        "job": org.job(),
    }
    headers = {"Idempotency-Key": "emp-1"}
    first = await api.post("/v1/employees", body, headers=headers)
    again = await api.post("/v1/employees", body, headers=headers)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert (await api.get("/v1/employees")).json()["items"][0]["id"] == first.json()["id"]
    assert len((await api.get("/v1/employees")).json()["items"]) == 1

    duplicate = await api.post("/v1/employees", {**body, "first_name": "Other"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_email"

    archived = await api.post(f"/v1/org/designations/{org.designation_2}/archive")
    assert archived.status_code == 200
    refused = await api.post(
        "/v1/employees",
        {**body, "work_email": None, "job": org.job(designation_id=org.designation_2)},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "archived_reference"
    unknown = await api.post(
        "/v1/employees",
        {**body, "work_email": None, "job": org.job(manager_employee_id=org.entity)},
    )
    assert unknown.status_code == 422


async def test_status_moves_forward_only(make_api: MakeApi, tenant: Tenant) -> None:
    api, org = await hr(make_api, tenant)
    draft = await make_employee(api, org, "Dev", activate=False)
    pre = await api.post(
        f"/v1/employees/{draft['id']}/status",
        {"to": "pre_boarding", "row_version": draft["row_version"]},
    )
    assert pre.status_code == 200
    stale = await api.post(
        f"/v1/employees/{draft['id']}/status",
        {"to": "active", "row_version": draft["row_version"]},
    )
    assert stale.status_code == 409
    active = await api.post(
        f"/v1/employees/{draft['id']}/status",
        {"to": "active", "row_version": pre.json()["row_version"]},
    )
    assert active.json()["status"] == "active"
    back = await api.post(
        f"/v1/employees/{draft['id']}/status",
        {"to": "pre_boarding", "row_version": active.json()["row_version"]},
    )
    assert back.status_code == 422
    assert back.json()["error"]["code"] == "invalid_transition"

    future = await make_employee(
        api, org, "Future", joining=today() + datetime.timedelta(days=30), activate=False
    )
    too_early = await api.post(
        f"/v1/employees/{future['id']}/status",
        {"to": "active", "row_version": future["row_version"]},
    )
    assert too_early.status_code == 422
    assert too_early.json()["error"]["code"] == "before_joining"


async def test_list_search_and_pagination(make_api: MakeApi, tenant: Tenant) -> None:
    api, org = await hr(make_api, tenant)
    for name in ("Anita", "Bala", "Chitra", "Deepak"):
        await make_employee(api, org, name)
    page = (await api.get("/v1/employees", params={"limit": 3})).json()
    assert [e["first_name"] for e in page["items"]] == ["Anita", "Bala", "Chitra"]
    rest = (
        await api.get("/v1/employees", params={"limit": 3, "cursor": page["next_cursor"]})
    ).json()
    assert [e["first_name"] for e in rest["items"]] == ["Deepak"]
    assert rest["next_cursor"] is None
    found = (await api.get("/v1/employees", params={"q": "chit"})).json()["items"]
    assert [e["first_name"] for e in found] == ["Chitra"]
    by_dept = (await api.get("/v1/employees", params={"department_id": org.department_2})).json()[
        "items"
    ]
    assert by_dept == []
    # `%` in a search is literal.
    assert (await api.get("/v1/employees", params={"q": "%"})).json()["items"] == []


# --- job history -----------------------------------------------------------------------------


async def test_promotion_closes_the_old_record_and_never_overwrites(
    make_api: MakeApi, tenant: Tenant
) -> None:
    api, org = await hr(make_api, tenant)
    employee = await make_employee(api, org, "Kiran")
    effective = today() - datetime.timedelta(days=10)
    changed = await api.post(
        f"/v1/employees/{employee['id']}/job-records",
        {
            "effective_from": effective.isoformat(),
            "reason": "promotion",
            "designation_id": org.designation_2,
            "grade_id": org.grade,
        },
    )
    assert changed.status_code == 201, changed.text
    assert changed.json()["designation_id"] == org.designation_2
    assert changed.json()["is_current"] is True

    records = (await api.get(f"/v1/employees/{employee['id']}/job-records")).json()
    assert [r["change_reason"] for r in records] == ["promotion", "hire"]
    old = records[1]
    assert old["designation_id"] == org.designation  # history kept
    assert old["valid_to"] == (effective - datetime.timedelta(days=1)).isoformat()
    assert old["is_current"] is False
    got = (await api.get(f"/v1/employees/{employee['id']}")).json()
    assert got["job"]["designation_name"] == "Senior Engineer"


async def test_a_future_dated_promotion_waits_for_its_day(
    make_api: MakeApi, tenant: Tenant
) -> None:
    api, org = await hr(make_api, tenant)
    employee = await make_employee(api, org, "Lata")
    boss = await make_employee(api, org, "Boss")
    start = today() + datetime.timedelta(days=30)
    scheduled = await api.post(
        f"/v1/employees/{employee['id']}/job-records",
        {
            "effective_from": start.isoformat(),
            "reason": "promotion",
            "designation_id": org.designation_2,
            "manager_employee_id": boss["id"],
        },
    )
    assert scheduled.status_code == 201, scheduled.text
    assert scheduled.json()["is_current"] is False
    got = (await api.get(f"/v1/employees/{employee['id']}")).json()
    assert got["job"]["designation_name"] == "Engineer"  # still the old job today
    assert got["job"]["manager_employee_id"] is None
    # …and the reporting tree doesn't change until then.
    reports = (await api.get("/v1/employees", params={"manager_id": boss["id"]})).json()
    assert reports["items"] == []
    records = (await api.get(f"/v1/employees/{employee['id']}/job-records")).json()
    assert records[0]["valid_from"] == start.isoformat()
    assert records[1]["valid_to"] == (start - datetime.timedelta(days=1)).isoformat()


async def test_job_change_rules(make_api: MakeApi, tenant: Tenant) -> None:
    api, org = await hr(make_api, tenant)
    employee = await make_employee(api, org, "Neha", joining=today() - datetime.timedelta(days=50))
    path = f"/v1/employees/{employee['id']}/job-records"

    same_day = await api.post(
        path,
        {
            "effective_from": (today() - datetime.timedelta(days=50)).isoformat(),
            "reason": "transfer",
            "location_id": org.location_2,
        },
    )
    assert same_day.status_code == 409
    assert same_day.json()["error"]["code"] == "record_starts_that_day"
    before = await api.post(
        path,
        {
            "effective_from": (today() - datetime.timedelta(days=60)).isoformat(),
            "reason": "transfer",
            "location_id": org.location_2,
        },
    )
    assert before.json()["error"]["code"] == "before_hire"
    nothing = await api.post(
        path,
        {"effective_from": today().isoformat(), "reason": "transfer", "location_id": org.location},
    )
    assert nothing.json()["error"]["code"] == "no_change"

    ok = await api.post(
        path,
        {
            "effective_from": today().isoformat(),
            "reason": "transfer",
            "location_id": org.location_2,
        },
    )
    assert ok.status_code == 201
    # A second change in the middle of the first one's life splits it again.
    middle = await api.post(
        path,
        {
            "effective_from": (today() - datetime.timedelta(days=20)).isoformat(),
            "reason": "redesignation",
            "designation_id": org.designation_2,
        },
    )
    assert middle.status_code == 201, middle.text
    records = (await api.get(path)).json()
    assert [r["valid_from"] for r in records] == sorted(
        (r["valid_from"] for r in records), reverse=True
    )
    # The periods tile the timeline: each starts the day after the previous one ends.
    ordered = list(reversed(records))
    for before_rec, after_rec in itertools.pairwise(ordered):
        assert datetime.date.fromisoformat(before_rec["valid_to"]) + datetime.timedelta(
            days=1
        ) == datetime.date.fromisoformat(after_rec["valid_from"])
    assert ordered[-1]["valid_to"] is None
    # The split-off tail kept what the earlier record had decided about the location.
    assert ordered[-1]["location_id"] == org.location_2


async def test_correcting_a_record_in_place(make_api: MakeApi, tenant: Tenant) -> None:
    api, org = await hr(make_api, tenant)
    employee = await make_employee(api, org, "Om")
    start = today() - datetime.timedelta(days=10)
    changed = (
        await api.post(
            f"/v1/employees/{employee['id']}/job-records",
            {
                "effective_from": start.isoformat(),
                "reason": "promotion",
                "designation_id": org.designation_2,
            },
        )
    ).json()
    # The promotion really took effect a week earlier: move its start and fix the grade.
    new_start = start - datetime.timedelta(days=7)
    corrected = await api.put(
        f"/v1/job-records/{changed['id']}",
        {
            "row_version": changed["row_version"],
            "start": new_start.isoformat(),
            "grade_id": org.grade,
        },
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["valid_from"] == new_start.isoformat()
    assert corrected.json()["grade_id"] == org.grade
    assert corrected.json()["change_reason"] == "promotion"
    records = (await api.get(f"/v1/employees/{employee['id']}/job-records")).json()
    assert records[1]["valid_to"] == (new_start - datetime.timedelta(days=1)).isoformat()
    stale = await api.put(
        f"/v1/job-records/{changed['id']}", {"row_version": changed["row_version"]}
    )
    assert stale.status_code == 409
    first = records[1]
    refused = await api.put(
        f"/v1/job-records/{first['id']}",
        {"row_version": first["row_version"], "start": new_start.isoformat()},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "first_record"


# --- the reporting hierarchy -----------------------------------------------------------------


async def test_manager_changes_keep_the_closure_exact(
    make_api: MakeApi, env: Env, tenant: Tenant, api_db: Database
) -> None:
    api, org = await hr(make_api, tenant)
    ceo = await make_employee(api, org, "Ceo")
    vp = await make_employee(api, org, "Vp", job={"manager_employee_id": ceo["id"]})
    lead = await make_employee(api, org, "Lead", job={"manager_employee_id": vp["id"]})
    dev = await make_employee(api, org, "Dev", job={"manager_employee_id": lead["id"]})
    other_vp = await make_employee(api, org, "Other", job={"manager_employee_id": ceo["id"]})

    def pairs(*triples: tuple[dict[str, str], dict[str, str], int]) -> set[tuple[str, str, int]]:
        return {(a["id"], d["id"], depth) for a, d, depth in triples}

    everyone = [ceo, vp, lead, dev, other_vp]
    selfs = pairs(*[(e, e, 0) for e in everyone])
    chain = pairs(
        (ceo, vp, 1),
        (ceo, lead, 2),
        (ceo, dev, 3),
        (vp, lead, 1),
        (vp, dev, 2),
        (lead, dev, 1),
        (ceo, other_vp, 1),
    )
    assert await closure(env, tenant) == selfs | chain

    # Move Lead (with Dev) under the other VP.
    moved = await api.post(
        f"/v1/employees/{lead['id']}/job-records",
        {
            "effective_from": today().isoformat(),
            "reason": "manager_change",
            "manager_employee_id": other_vp["id"],
        },
    )
    assert moved.status_code == 201, moved.text
    expected = selfs | pairs(
        (ceo, vp, 1),
        (ceo, other_vp, 1),
        (ceo, lead, 2),
        (ceo, dev, 3),
        (other_vp, lead, 1),
        (other_vp, dev, 2),
        (lead, dev, 1),
    )
    assert await closure(env, tenant) == expected

    # A full rebuild from the job records gives exactly the same table.
    async with api_db.tenant_session(
        RequestContext(ActorType.WORKER, tenant.id, None, None, None)
    ) as session:
        await hierarchy.rebuild(session)
    assert await closure(env, tenant) == expected

    # Removing the manager makes Lead a root.
    cleared = await api.post(
        f"/v1/employees/{lead['id']}/job-records",
        {
            "effective_from": (today() + datetime.timedelta(days=0)).isoformat(),
            "reason": "manager_change",
            "manager_employee_id": None,
        },
    )
    assert cleared.status_code in (201, 409)  # the record already starts today: correct it
    if cleared.status_code == 409:
        current = (await api.get(f"/v1/employees/{lead['id']}/job-records")).json()[0]
        fixed = await api.put(
            f"/v1/job-records/{current['id']}",
            {"row_version": current["row_version"], "manager_employee_id": None},
        )
        assert fixed.status_code == 200, fixed.text
    final = await closure(env, tenant)
    assert (other_vp["id"], lead["id"], 1) not in final
    assert (lead["id"], dev["id"], 1) in final
    assert (ceo["id"], dev["id"], 3) not in final


async def test_a_reporting_cycle_is_refused(make_api: MakeApi, tenant: Tenant) -> None:
    api, org = await hr(make_api, tenant)
    boss = await make_employee(api, org, "Boss")
    report = await make_employee(api, org, "Report", job={"manager_employee_id": boss["id"]})
    cycle = await api.post(
        f"/v1/employees/{boss['id']}/job-records",
        {
            "effective_from": today().isoformat(),
            "reason": "manager_change",
            "manager_employee_id": report["id"],
        },
    )
    assert cycle.status_code == 422
    assert cycle.json()["error"]["code"] == "reporting_cycle"
    self_manager = await api.post(
        f"/v1/employees/{boss['id']}/job-records",
        {
            "effective_from": today().isoformat(),
            "reason": "manager_change",
            "manager_employee_id": boss["id"],
        },
    )
    assert self_manager.status_code == 422


async def test_the_manager_role_follows_the_reporting_tree(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api, org = await hr(make_api, tenant)
    boss_account = await env.add_account(tenant, "employee")
    boss = await link(api, await make_employee(api, org, "Boss"), boss_account)
    await make_employee(api, org, "Report", job={"manager_employee_id": boss["id"]})

    async def manager_assignments() -> int:
        rows = await env.sql(
            "SELECT count(*) FROM platform.role_assignments ra JOIN platform.roles r "
            "ON r.tenant_id = ra.tenant_id AND r.id = ra.role_id "
            "WHERE ra.membership_id = :m AND r.key = 'manager' AND ra.scope_type = 'all_reports'",
            m=boss_account.membership_id,
        )
        return int(rows[0][0])

    assert await manager_assignments() == 1
    boss_api = await (await make_api()).sign_in(boss_account)
    me = (await boss_api.get("/v1/me")).json()
    assert "core.employees.read" in me["permissions"]

    # With no reports left, the automatic grant goes away.
    report = (await api.get("/v1/employees", params={"manager_id": boss["id"]})).json()["items"][0]
    record = (await api.get(f"/v1/employees/{report['id']}/job-records")).json()[0]
    fixed = await api.put(
        f"/v1/job-records/{record['id']}",
        {"row_version": record["row_version"], "manager_employee_id": None},
    )
    assert fixed.status_code == 200
    assert await manager_assignments() == 0
