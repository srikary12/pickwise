# SPDX-License-Identifier: AGPL-3.0-only
"""The data-scope matrix: what each scope shows, for every employee-scoped surface."""

import datetime
from typing import Any

import pytest

from tests.integration.conftest import MakeApi
from tests.integration.core_support import (
    Org,
    link,
    make_employee,
    make_org,
    post,
    scoped_account,
    today,
)
from tests.integration.support import Account, Api, Env, Tenant

pytestmark = pytest.mark.db


class World:
    """A small company: two departments (one with a sub-department), two cities, a reporting chain."""

    org: Org
    plat_department: str
    people: dict[str, dict[str, Any]]


async def build(make_api: MakeApi, tenant: Tenant) -> tuple[Api, World]:
    api = await (await make_api()).sign_in(tenant.admin)
    world = World()
    world.org = org = await make_org(api)
    world.plat_department = (
        await post(
            api,
            "/v1/org/departments",
            {"code": "PLAT", "name": "Platform", "parent_id": org.department},
        )
    )["id"]
    boss = await make_employee(api, org, "Boss")
    eng_blr = await make_employee(api, org, "EngBlr", job={"manager_employee_id": boss["id"]})
    plat_blr = await make_employee(
        api,
        org,
        "PlatBlr",
        job={"department_id": world.plat_department, "manager_employee_id": eng_blr["id"]},
    )
    sales_hyd = await make_employee(
        api, org, "SalesHyd", job={"department_id": org.department_2, "location_id": org.location_2}
    )
    eng_hyd = await make_employee(api, org, "EngHyd", job={"location_id": org.location_2})
    world.people = {
        "Boss": boss,
        "EngBlr": eng_blr,
        "PlatBlr": plat_blr,
        "SalesHyd": sales_hyd,
        "EngHyd": eng_hyd,
    }
    return api, world


async def names(api: Api, path: str = "/v1/employees") -> set[str]:
    response = await api.get(path, params={"limit": 200})
    assert response.status_code == 200, response.text
    return {e["first_name"] for e in response.json()["items"]}


ALL = {"Boss", "EngBlr", "PlatBlr", "SalesHyd", "EngHyd"}


async def view_as(make_api: MakeApi, account: Account) -> Api:
    return await (await make_api()).sign_in(account)


async def test_tenant_legal_entity_location_and_department_scopes(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    _, w = await build(make_api, tenant)
    cases = {
        ("tenant", None): ALL,
        ("legal_entity", w.org.entity): ALL,
        ("location", w.org.location): {"Boss", "EngBlr", "PlatBlr"},
        ("location", w.org.location_2): {"SalesHyd", "EngHyd"},
        ("department", w.org.department): {"Boss", "EngBlr", "EngHyd"},  # exactly ENG
        ("department", w.plat_department): {"PlatBlr"},
        ("department_subtree", w.org.department): {"Boss", "EngBlr", "EngHyd", "PlatBlr"},
        ("department_subtree", w.plat_department): {"PlatBlr"},
        ("department_subtree", w.org.department_2): {"SalesHyd"},
    }
    for (scope, scope_id), expected in cases.items():
        account = await scoped_account(env, tenant, "hr_ops", scope, scope_id)
        viewer = await view_as(make_api, account)
        assert await names(viewer) == expected, (scope, scope_id)


async def test_report_scopes_follow_the_reporting_tree(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin, w = await build(make_api, tenant)
    boss = w.people["Boss"]

    for scope, expected in (
        ("direct_reports", {"EngBlr"}),
        ("all_reports", {"EngBlr", "PlatBlr"}),
        ("self", {"Boss"}),
    ):
        account = await scoped_account(env, tenant, "hr_ops", scope)
        await link(admin, (await admin.get(f"/v1/employees/{boss['id']}")).json(), account)
        # Linking grants the automatic manager role too; this test is about the scope alone.
        await env.sql(
            "DELETE FROM platform.role_assignments ra USING platform.roles r "
            "WHERE r.id = ra.role_id AND r.key = 'manager' AND ra.membership_id = :m",
            m=account.membership_id,
        )
        viewer = await view_as(make_api, account)
        assert await names(viewer) == expected, scope
        # Detail pages follow the same rule: outside the scope is a 404, not a 403.
        outside = w.people["SalesHyd"]["id"]
        assert (await viewer.get(f"/v1/employees/{outside}")).status_code == 404
        # Free the employee for the next account.
        refreshed = (await admin.get(f"/v1/employees/{boss['id']}")).json()
        unlinked = await admin.put(
            f"/v1/employees/{boss['id']}/user",
            {"membership_id": None, "row_version": refreshed["row_version"]},
        )
        assert unlinked.status_code == 200


async def test_manager_role_shows_reports_but_no_personal_data(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin, w = await build(make_api, tenant)
    account = await env.add_account(tenant, "employee")
    await link(admin, (await admin.get(f"/v1/employees/{w.people['Boss']['id']}")).json(), account)
    manager = await view_as(make_api, account)

    assert await names(manager) == {"EngBlr", "PlatBlr"}
    report = w.people["EngBlr"]["id"]
    assert (await manager.get(f"/v1/employees/{report}")).status_code == 200
    assert (await manager.get(f"/v1/employees/{report}/job-records")).status_code == 200
    # Personal, identity and bank tabs need their own permissions, which managers don't hold.
    for tab in (
        "personal",
        "addresses",
        "emergency-contacts",
        "dependents",
        "identity",
        "bank-accounts",
    ):
        assert (await manager.get(f"/v1/employees/{report}/{tab}")).status_code == 403, tab
    # Nor can they change anything.
    assert (
        await manager.post(
            f"/v1/employees/{report}/job-records",
            {
                "effective_from": today().isoformat(),
                "reason": "transfer",
                "location_id": w.org.location_2,
            },
        )
    ).status_code == 403
    assert (await manager.get(f"/v1/employees/{w.people['SalesHyd']['id']}")).status_code == 404


async def test_plain_employees_see_the_directory_but_not_records(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    await build(make_api, tenant)
    account = await env.add_account(tenant, "employee")
    viewer = await view_as(make_api, account)
    assert (await viewer.get("/v1/employees")).status_code == 403
    directory = (await viewer.get("/v1/directory", params={"limit": 200})).json()["items"]
    assert {e["display_name"].split()[0] for e in directory} == ALL
    first = directory[0]
    assert set(first) == {
        "id",
        "employee_code",
        "display_name",
        "work_email",
        "work_phone",
        "photo_file_id",
        "designation_name",
        "department_id",
        "department_name",
        "location_name",
        "manager_employee_id",
        "manager_name",
    }
    chart = (await viewer.get("/v1/directory/org-chart")).json()["nodes"]
    boss_node = next(n for n in chart if n["display_name"].startswith("Boss"))
    assert boss_node["report_count"] == 1
    # Search finds colleagues, with only name, code and job.
    hits = (await viewer.get("/v1/search", params={"q": "Eng", "kinds": "employee"})).json()
    group = next(g for g in hits["groups"] if g["kind"] == "employee")
    assert {h["title"] for h in group["hits"]} >= {"EngBlr Tester", "EngHyd Tester"}


async def test_employees_who_have_left_leave_the_directory(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin, w = await build(make_api, tenant)
    await env.sql(
        "UPDATE core.employees SET status = 'exited', date_of_exit = :d WHERE id = :id",
        d=today() - datetime.timedelta(days=1),
        id=w.people["EngHyd"]["id"],
    )
    viewer = await view_as(make_api, await env.add_account(tenant, "employee"))
    listed = {
        e["display_name"].split()[0] for e in (await viewer.get("/v1/directory")).json()["items"]
    }
    assert "EngHyd" not in listed
    assert "EngHyd" in await names(admin)  # HR still sees them


async def test_writes_respect_scope(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    _, w = await build(make_api, tenant)
    account = await scoped_account(env, tenant, "hr_ops", "location", w.org.location_2)
    viewer = await view_as(make_api, account)
    mine = w.people["EngHyd"]
    other = w.people["EngBlr"]
    body = {"first_name": "Renamed", "row_version": mine["row_version"]}
    assert (await viewer.put(f"/v1/employees/{mine['id']}", body)).status_code == 200
    refused = await viewer.put(
        f"/v1/employees/{other['id']}", {"first_name": "Nope", "row_version": other["row_version"]}
    )
    assert refused.status_code == 404
