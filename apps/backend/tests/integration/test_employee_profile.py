# SPDX-License-Identifier: AGPL-3.0-only
"""Profile tabs, self-service, documents and approver resolution."""

import datetime
import uuid
from typing import Any

import pytest

from pickwise.core.registrations import resolve_dept_head, resolve_manager, resolve_skip_level
from pickwise.platform.approvals.registry import ResolveContext
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database
from tests.integration.conftest import MakeApi
from tests.integration.core_support import Org, link, make_employee, make_org, today
from tests.integration.support import Api, Env, Tenant
from tests.integration.test_files import PDF, PDF_TYPE, fetch, upload

pytestmark = pytest.mark.db


async def setup(make_api: MakeApi, tenant: Tenant) -> tuple[Api, Org, dict[str, Any]]:
    api = await (await make_api()).sign_in(tenant.admin)
    org = await make_org(api)
    return api, org, await make_employee(api, org, "Ann")


# --- personal, addresses, contacts, family ---------------------------------------------------


async def test_personal_details_round_trip(make_api: MakeApi, tenant: Tenant) -> None:
    api, _, ann = await setup(make_api, tenant)
    path = f"/v1/employees/{ann['id']}/personal"
    empty = (await api.get(path)).json()
    assert empty["row_version"] is None
    assert empty["date_of_birth"] is None
    body = {
        "date_of_birth": "1992-03-14",
        "gender": "female",
        "marital_status": "married",
        "blood_group": "O+",
        "nationality": "Indian",
        "father_or_spouse_name": "R Tester",
        "personal_email": "ann@home.test",
        "personal_phone": "+91 98765 43210",
    }
    saved = await api.put(path, body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["personal_email"] == "ann@home.test"
    version = saved.json()["row_version"]
    assert (await api.put(path, body)).status_code == 409  # omitted version after the first save
    updated = await api.put(path, {**body, "blood_group": "A+", "row_version": version})
    assert updated.json()["blood_group"] == "A+"
    stale = await api.put(path, {**body, "row_version": version})
    assert stale.status_code == 409
    future = await api.put(
        path, {**body, "date_of_birth": "2999-01-01", "row_version": updated.json()["row_version"]}
    )
    assert future.status_code == 422


async def test_addresses_are_effective_dated(make_api: MakeApi, tenant: Tenant) -> None:
    api, _, ann = await setup(make_api, tenant)
    path = f"/v1/employees/{ann['id']}/addresses"
    home = {"line1": "12 MG Road", "city": "Bengaluru", "state_code": "IN-KA", "pincode": "560001"}
    first = await api.put(
        f"{path}/current",
        {**home, "valid_from": (today() - datetime.timedelta(days=90)).isoformat()},
    )
    assert first.status_code == 200, first.text
    moved = await api.put(
        f"{path}/current",
        {**home, "line1": "7 Banjara Hills", "city": "Hyderabad", "state_code": "IN-TS"},
    )
    assert moved.status_code == 200
    rows = (await api.get(path)).json()
    assert [r["line1"] for r in rows] == ["7 Banjara Hills", "12 MG Road"]
    assert rows[1]["valid_to"] == (today() - datetime.timedelta(days=1)).isoformat()
    assert rows[0]["valid_to"] is None
    assert (await api.put(f"{path}/permanent", {**home})).status_code == 200
    assert (await api.put(f"{path}/office", {**home})).status_code == 422
    bad = await api.put(f"{path}/current", {**home, "pincode": "012345"})
    assert bad.status_code == 422


async def test_contacts_dependents_and_nominations(make_api: MakeApi, tenant: Tenant) -> None:
    api, _, ann = await setup(make_api, tenant)
    base = f"/v1/employees/{ann['id']}"
    one = (
        await api.post(
            f"{base}/emergency-contacts",
            {"name": "Raj", "relationship": "spouse", "phone": "9876543210", "is_primary": True},
        )
    ).json()
    two = (
        await api.post(
            f"{base}/emergency-contacts",
            {"name": "Sita", "relationship": "mother", "phone": "9876500000", "is_primary": True},
        )
    ).json()
    contacts = (await api.get(f"{base}/emergency-contacts")).json()
    assert [(c["name"], c["is_primary"]) for c in contacts] == [("Sita", True), ("Raj", False)]
    raj = next(c for c in contacts if c["name"] == "Raj")  # its version moved when Sita took over
    assert raj["row_version"] != one["row_version"]
    edited = await api.put(
        f"{base}/emergency-contacts/{one['id']}",
        {**one, "is_primary": True, "row_version": raj["row_version"]},
    )
    assert edited.status_code == 200
    assert {
        c["name"]: c["is_primary"] for c in (await api.get(f"{base}/emergency-contacts")).json()
    } == {
        "Raj": True,
        "Sita": False,
    }
    assert (await api.delete(f"{base}/emergency-contacts/{two['id']}")).status_code == 204

    spouse = (
        await api.post(f"{base}/dependents", {"name": "Raj", "relationship": "spouse"})
    ).json()
    child = (
        await api.post(
            f"{base}/dependents",
            {"name": "Kid", "relationship": "child", "date_of_birth": "2018-05-05"},
        )
    ).json()
    path = f"{base}/nominations/pf"
    bad_total = await api.put(
        path,
        {
            "shares": [
                {"dependent_id": spouse["id"], "share_percent": "60"},
                {"dependent_id": child["id"], "share_percent": "30"},
            ]
        },
    )
    assert bad_total.status_code == 422
    ok = await api.put(
        path,
        {
            "shares": [
                {"dependent_id": spouse["id"], "share_percent": "60.50"},
                {"dependent_id": child["id"], "share_percent": "39.50"},
            ]
        },
    )
    assert ok.status_code == 200, ok.text
    assert {n["dependent_id"]: n["share_percent"] for n in ok.json()} == {
        spouse["id"]: "60.50",
        child["id"]: "39.50",
    }
    # Someone nominated can't be removed until the nomination changes.
    blocked = await api.delete(f"{base}/dependents/{child['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "in_use"
    stranger = await api.put(
        path, {"shares": [{"dependent_id": str(uuid.uuid4()), "share_percent": "100"}]}
    )
    assert stranger.status_code == 422
    cleared = await api.put(path, {"shares": []})
    assert cleared.json() == []
    assert (await api.delete(f"{base}/dependents/{child['id']}")).status_code == 204


async def test_education_and_experience(make_api: MakeApi, tenant: Tenant) -> None:
    api, _, ann = await setup(make_api, tenant)
    base = f"/v1/employees/{ann['id']}"
    edu = (
        await api.post(
            f"{base}/education",
            {"institution": "IIT Madras", "degree": "B.Tech", "start_year": 2010, "end_year": 2014},
        )
    ).json()
    backwards = await api.post(
        f"{base}/education",
        {"institution": "X", "degree": "Y", "start_year": 2014, "end_year": 2010},
    )
    assert backwards.status_code == 422
    edited = await api.put(
        f"{base}/education/{edu['id']}",
        {"institution": "IIT Madras", "degree": "M.Tech", "row_version": edu["row_version"]},
    )
    assert edited.json()["degree"] == "M.Tech"
    exp = (
        await api.post(
            f"{base}/experience",
            {
                "employer": "Initech",
                "title": "Dev",
                "from_date": "2014-07-01",
                "to_date": "2018-06-30",
            },
        )
    ).json()
    assert [e["employer"] for e in (await api.get(f"{base}/experience")).json()] == ["Initech"]
    assert (await api.delete(f"{base}/experience/{exp['id']}")).status_code == 204
    assert (await api.get(f"{base}/experience")).json() == []


# --- documents -------------------------------------------------------------------------------


async def test_documents_and_who_may_download_them(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api, _, ann = await setup(make_api, tenant)
    account = await env.add_account(tenant, "employee")
    await link(api, ann, account)
    base = f"/v1/employees/{ann['id']}/documents"

    async def uploaded(name: str) -> dict[str, str]:
        file = await upload(
            api,
            env,
            content=PDF,
            name=name,
            mime=PDF_TYPE,
            classification="confidential",
            owner_entity_type="employee_document",
            owner_entity_id=ann["id"],
        )
        await env.scan_file(tenant.id, uuid.UUID(file["id"]))
        return {"id": file["id"]}

    shared = await uploaded("offer.pdf")
    private = await uploaded("review.pdf")
    for file, title, visible in ((shared, "Offer letter", True), (private, "Review", False)):
        created = await api.post(
            base,
            {
                "category": "offer_letter",
                "file_id": file["id"],
                "title": title,
                "visible_to_employee": visible,
            },
        )
        assert created.status_code == 201, created.text
    assert len((await api.get(base)).json()) == 2

    me = await (await make_api()).sign_in(account)
    mine = (await me.get("/v1/me/employee/documents")).json()
    assert [d["title"] for d in mine] == ["Offer letter"]
    ok = await me.get(f"/v1/files/{shared['id']}/download")
    assert ok.status_code == 200
    assert (await fetch(ok.json()["url"])).content == PDF
    assert (await me.get(f"/v1/files/{private['id']}/download")).status_code in (403, 404)

    # A file uploaded for someone else can't be attached here.
    foreign = await upload(
        api,
        env,
        content=PDF,
        name="x.pdf",
        mime=PDF_TYPE,
        classification="confidential",
        owner_entity_type="employee_document",
        owner_entity_id=str(uuid.uuid4()),
    )
    await env.scan_file(tenant.id, uuid.UUID(foreign["id"]))
    refused = await api.post(
        base, {"category": "other", "file_id": foreign["id"], "title": "Wrong"}
    )
    assert refused.status_code == 422


# --- self-service ----------------------------------------------------------------------------


async def test_a_person_sees_and_edits_only_their_own_record(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api, org, ann = await setup(make_api, tenant)
    bob = await make_employee(api, org, "Bob")
    account = await env.add_account(tenant, "employee")
    await link(api, ann, account)
    me = await (await make_api()).sign_in(account)

    own = (await me.get("/v1/me/employee")).json()
    assert own["id"] == ann["id"]
    assert (await me.get("/v1/me/employee/job-records")).status_code == 200
    changed = await me.put(
        "/v1/me/employee/contact-details",
        {"personal_email": "ann@home.test", "personal_phone": "9876543210"},
    )
    assert changed.status_code == 200
    assert changed.json()["personal_email"] == "ann@home.test"
    address = await me.put(
        "/v1/me/employee/addresses/current",
        {"line1": "1 Lake Road", "city": "Pune", "state_code": "IN-MH", "pincode": "411001"},
    )
    assert address.status_code == 200
    contact = await me.post(
        "/v1/me/employee/emergency-contacts",
        {"name": "Raj", "relationship": "spouse", "phone": "9876543210", "is_primary": True},
    )
    assert contact.status_code == 201
    assert (await me.get("/v1/me/employee/emergency-contacts")).json()[0]["name"] == "Raj"

    # The same person can't read the HR view of themselves or anyone else, and can't change
    # fields the self-service API doesn't offer.
    assert (await me.get(f"/v1/employees/{ann['id']}")).status_code == 403
    assert (await me.get(f"/v1/employees/{bob['id']}/personal")).status_code == 403
    assert (
        await me.put(f"/v1/employees/{ann['id']}", {"first_name": "X", "row_version": 1})
    ).status_code == 403

    # Someone with no employee record gets a clear answer.
    nobody = await (await make_api()).sign_in(await env.add_account(tenant, "employee"))
    gone = await nobody.get("/v1/me/employee")
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "no_employee_record"
    # An employee can't be linked to two people, or one person to two employees.
    other = await env.add_account(tenant, "employee")
    clash = await api.put(
        f"/v1/employees/{bob['id']}/user",
        {"membership_id": str(account.membership_id), "row_version": bob["row_version"]},
    )
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "membership_taken"
    assert other.membership_id != account.membership_id


# --- approvers -------------------------------------------------------------------------------


async def test_approver_resolvers_follow_the_org(
    make_api: MakeApi, env: Env, tenant: Tenant, api_db: Database
) -> None:
    api = await (await make_api()).sign_in(tenant.admin)
    org = await make_org(api)
    accounts = {n: await env.add_account(tenant, "employee") for n in ("ceo", "vp", "dev")}
    ceo = await link(api, await make_employee(api, org, "Ceo"), accounts["ceo"])
    vp = await link(
        api,
        await make_employee(api, org, "Vp", job={"manager_employee_id": ceo["id"]}),
        accounts["vp"],
    )
    dev = await link(
        api,
        await make_employee(api, org, "Dev", job={"manager_employee_id": vp["id"]}),
        accounts["dev"],
    )
    # The department's head is the CEO.
    department = (await api.get("/v1/org/departments")).json()[0]
    await env.sql(
        "UPDATE core.departments SET head_employee_id = :h WHERE id = :d",
        h=ceo["id"],
        d=department["id"],
    )
    assert dev["id"]

    def ctx(requester: str, **extra: Any) -> ResolveContext:
        return ResolveContext(
            tenant_id=tenant.id,
            entity_type="separation",
            entity_id=uuid.uuid4(),
            attributes={},
            requested_by=accounts[requester].user_id,
            **extra,
        )

    async with api_db.tenant_session(
        RequestContext(ActorType.WORKER, tenant.id, None, None, None)
    ) as session:
        assert await resolve_manager(session, ctx("dev")) == [accounts["vp"].user_id]
        assert await resolve_skip_level(session, ctx("dev")) == [accounts["ceo"].user_id]
        assert await resolve_manager(session, ctx("ceo")) == []  # nobody above
        assert await resolve_dept_head(session, ctx("dev")) == [accounts["ceo"].user_id]
        # Escalating from the VP means the VP's manager.
        escalated = ctx("dev", escalating_from=accounts["vp"].user_id)
        assert await resolve_skip_level(session, escalated) == []
        escalated_dev = ctx("dev", escalating_from=accounts["dev"].user_id)
        assert await resolve_skip_level(session, escalated_dev) == [accounts["ceo"].user_id]
