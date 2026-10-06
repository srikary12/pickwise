# SPDX-License-Identifier: AGPL-3.0-only
"""The org API: permissions, validation, concurrency, idempotency and the department tree."""

import uuid
from typing import Any

import pytest

from tests.integration.conftest import MakeApi
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db

ENTITY = {"name": "Acme India", "legal_name": "Acme India Private Limited", "pan": "AABCA1234F"}


async def admin(make_api: MakeApi, tenant: Tenant) -> Api:
    return await (await make_api()).sign_in(tenant.admin)


async def make_entity(api: Api, **extra: Any) -> dict[str, Any]:
    created = await api.post("/v1/org/legal-entities", {**ENTITY, **extra})
    assert created.status_code == 201, created.text
    body: dict[str, Any] = created.json()
    return body


async def make_department(api: Api, code: str, parent: str | None = None) -> dict[str, Any]:
    created = await api.post(
        "/v1/org/departments", {"code": code, "name": code.title(), "parent_id": parent}
    )
    assert created.status_code == 201, created.text
    body: dict[str, Any] = created.json()
    return body


# --- permissions -----------------------------------------------------------------------------


async def test_everyone_can_read_but_only_hr_can_change(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    hr = await admin(make_api, tenant)
    await make_entity(hr)
    employee = await (await make_api()).sign_in(await env.add_account(tenant, "employee"))
    manager = await (await make_api()).sign_in(await env.add_account(tenant, "manager"))
    for who in (employee, manager):
        for path in ("legal-entities", "locations", "cost-centers", "designations", "grades"):
            assert (await who.get(f"/v1/org/{path}")).status_code == 200, path
        assert (await who.get("/v1/org/departments")).status_code == 200
        assert (await who.post("/v1/org/legal-entities", ENTITY)).status_code == 403
        assert (
            await who.post("/v1/org/designations", {"code": "X", "name": "X"})
        ).status_code == 403
    anonymous = await make_api()
    assert (await anonymous.get("/v1/org/grades")).status_code == 401


async def test_org_data_is_per_tenant(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    other = await env.create_tenant()
    mine = await admin(make_api, tenant)
    theirs = await admin(make_api, other)
    entity = await make_entity(mine)
    assert (await theirs.get("/v1/org/legal-entities")).json() == []
    assert (
        await theirs.put(f"/v1/org/legal-entities/{entity['id']}", {**ENTITY, "row_version": 1})
    ).status_code == 404
    # The same code is free in another tenant.
    assert (
        await theirs.post("/v1/org/designations", {"code": "SE", "name": "Engineer"})
    ).status_code == 201
    assert (
        await mine.post("/v1/org/designations", {"code": "SE", "name": "Engineer"})
    ).status_code == 201


# --- legal entities, registrations -----------------------------------------------------------


async def test_legal_entity_lifecycle_and_validation(make_api: MakeApi, tenant: Tenant) -> None:
    api = await admin(make_api, tenant)
    bad = await api.post("/v1/org/legal-entities", {**ENTITY, "pan": "not-a-pan"})
    assert bad.status_code == 422
    entity = await make_entity(api, gstin="29AABCA1234F1Z5")
    assert (await api.post("/v1/org/legal-entities", ENTITY)).status_code == 409  # same name

    updated = await api.put(
        f"/v1/org/legal-entities/{entity['id']}",
        {**ENTITY, "legal_name": "Acme India Pvt Ltd", "row_version": entity["row_version"]},
    )
    assert updated.status_code == 200
    assert updated.json()["legal_name"] == "Acme India Pvt Ltd"
    stale = await api.put(
        f"/v1/org/legal-entities/{entity['id']}",
        {**ENTITY, "row_version": entity["row_version"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_row_version"

    archived = await api.post(f"/v1/org/legal-entities/{entity['id']}/archive")
    assert archived.json()["archived_at"] is not None
    assert (await api.get("/v1/org/legal-entities")).json() == []
    listed = await api.get("/v1/org/legal-entities", params={"include_archived": "true"})
    assert [e["id"] for e in listed.json()] == [entity["id"]]
    assert (await api.post(f"/v1/org/legal-entities/{entity['id']}/unarchive")).json()[
        "archived_at"
    ] is None


async def test_registrations_reject_overlapping_periods(make_api: MakeApi, tenant: Tenant) -> None:
    api = await admin(make_api, tenant)
    entity = await make_entity(api)
    base = f"/v1/org/legal-entities/{entity['id']}/registrations"
    first = await api.post(
        base,
        {
            "registration_type": "PT",
            "state_code": "IN-KA",
            "registration_no": "PT-1",
            "valid_from": "2025-04-01",
            "valid_to": "2026-03-31",
        },
    )
    assert first.status_code == 201, first.text
    assert first.json()["valid_to"] == "2026-03-31"  # the last day, inclusive
    successor = {
        "registration_type": "PT",
        "state_code": "IN-KA",
        "registration_no": "PT-2",
        "valid_from": "2026-04-01",
        "valid_to": None,
    }
    assert (await api.post(base, successor)).status_code == 201
    overlap = await api.post(base, {**successor, "valid_from": "2026-03-01"})
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "registration_overlap"
    backwards = await api.post(
        base,
        {**successor, "state_code": "IN-TG", "valid_from": "2026-05-01", "valid_to": "2026-04-01"},
    )
    assert backwards.status_code == 422

    listed = (await api.get(base)).json()
    assert [r["registration_no"] for r in listed] == ["PT-1", "PT-2"]
    assert (await api.delete(f"{base}/{first.json()['id']}")).status_code == 204
    assert [r["registration_no"] for r in (await api.get(base)).json()] == ["PT-2"]


# --- locations, cost centres, grades ---------------------------------------------------------


async def test_locations_are_validated(make_api: MakeApi, tenant: Tenant) -> None:
    api = await admin(make_api, tenant)
    entity = await make_entity(api)
    body = {
        "legal_entity_id": entity["id"],
        "code": "BLR-HQ",
        "name": "Bengaluru HQ",
        "state_code": "IN-KA",
        "city": "Bengaluru",
        "pincode": "560001",
        "timezone": "Asia/Kolkata",
        "latitude": "12.971600",
        "longitude": "77.594600",
        "geofence_radius_m": 150,
    }
    created = await api.post("/v1/org/locations", body)
    assert created.status_code == 201, created.text
    assert (await api.post("/v1/org/locations", {**body, "code": "blr-hq"})).status_code == 409
    assert (
        await api.post("/v1/org/locations", {**body, "code": "B2", "timezone": "Mars/Base"})
    ).status_code == 422
    no_point = {k: v for k, v in body.items() if k not in ("latitude", "longitude")}
    assert (await api.post("/v1/org/locations", {**no_point, "code": "B3"})).status_code == 422
    unknown_entity = {**body, "code": "B4", "legal_entity_id": str(uuid.uuid4())}
    refused = await api.post("/v1/org/locations", unknown_entity)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "unknown_reference"


async def test_grades_keep_money_exact_and_ordered(make_api: MakeApi, tenant: Tenant) -> None:
    api = await admin(make_api, tenant)
    senior = await api.post(
        "/v1/org/grades",
        {"code": "L5", "name": "Senior", "rank": 5, "ctc_min": "1800000.50", "ctc_max": "3000000"},
    )
    junior = await api.post("/v1/org/grades", {"code": "L2", "name": "Junior", "rank": 2})
    assert senior.status_code == 201
    assert junior.status_code == 201
    assert senior.json()["ctc_min"] == "1800000.50"
    assert [g["code"] for g in (await api.get("/v1/org/grades")).json()] == ["L2", "L5"]
    inverted = await api.post(
        "/v1/org/grades", {"code": "L9", "name": "X", "rank": 9, "ctc_min": "10", "ctc_max": "5"}
    )
    assert inverted.status_code == 422


async def test_creating_twice_with_one_idempotency_key_creates_once(
    make_api: MakeApi, tenant: Tenant
) -> None:
    api = await admin(make_api, tenant)
    body = {"code": "SE", "name": "Software Engineer"}
    headers = {"Idempotency-Key": "des-1"}
    first = await api.post("/v1/org/designations", body, headers=headers)
    again = await api.post("/v1/org/designations", body, headers=headers)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert len((await api.get("/v1/org/designations")).json()) == 1
    different = await api.post("/v1/org/designations", {**body, "name": "Other"}, headers=headers)
    assert different.status_code == 422


# --- departments -----------------------------------------------------------------------------


async def test_department_tree_move_and_cycle(make_api: MakeApi, tenant: Tenant) -> None:
    api = await admin(make_api, tenant)
    eng = await make_department(api, "eng")
    platform_ = await make_department(api, "plat", eng["id"])
    infra = await make_department(api, "infra", platform_["id"])
    sales = await make_department(api, "sales")
    listed = (await api.get("/v1/org/departments")).json()
    assert {d["code"]: d["depth"] for d in listed} == {"eng": 0, "plat": 1, "infra": 2, "sales": 0}
    # Parents come before their children.
    codes = [d["code"] for d in listed]
    assert codes.index("eng") < codes.index("plat") < codes.index("infra")

    moved = await api.post(
        f"/v1/org/departments/{platform_['id']}/move",
        {"parent_id": sales["id"], "row_version": platform_["row_version"]},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["parent_id"] == sales["id"]
    after = {d["code"]: d for d in (await api.get("/v1/org/departments")).json()}
    assert after["infra"]["path"].startswith(after["sales"]["path"] + ".")  # followed its parent
    assert after["infra"]["depth"] == 2

    # A stale version is a conflict; a cycle is refused.
    stale = await api.post(
        f"/v1/org/departments/{platform_['id']}/move",
        {"parent_id": None, "row_version": platform_["row_version"]},
    )
    assert stale.status_code == 409
    current = after["sales"]
    cycle = await api.post(
        f"/v1/org/departments/{sales['id']}/move",
        {"parent_id": infra["id"], "row_version": current["row_version"]},
    )
    assert cycle.status_code == 422
    assert cycle.json()["error"]["code"] == "department_cycle"
    self_parent = await api.post(
        f"/v1/org/departments/{sales['id']}/move",
        {"parent_id": sales["id"], "row_version": current["row_version"]},
    )
    assert self_parent.status_code == 422

    # Can't archive a department that still has active sub-departments.
    blocked = await api.post(f"/v1/org/departments/{sales['id']}/archive")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "has_active_children"
    assert (await api.post(f"/v1/org/departments/{infra['id']}/archive")).status_code == 200
    assert (await api.post(f"/v1/org/departments/{platform_['id']}/archive")).status_code == 200
    assert (await api.post(f"/v1/org/departments/{sales['id']}/archive")).status_code == 200
    restore_child = await api.post(f"/v1/org/departments/{platform_['id']}/unarchive")
    assert restore_child.status_code == 409  # its parent is archived
    assert restore_child.json()["error"]["code"] == "parent_archived"


async def test_department_with_unknown_parent_or_cost_centre(
    make_api: MakeApi, tenant: Tenant
) -> None:
    api = await admin(make_api, tenant)
    bad_parent = await api.post(
        "/v1/org/departments", {"code": "a", "name": "A", "parent_id": str(uuid.uuid4())}
    )
    assert bad_parent.status_code == 422
    bad_cc = await api.post(
        "/v1/org/departments", {"code": "b", "name": "B", "cost_center_id": str(uuid.uuid4())}
    )
    assert bad_cc.status_code == 422
