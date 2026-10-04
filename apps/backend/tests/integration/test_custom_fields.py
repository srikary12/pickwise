# SPDX-License-Identifier: AGPL-3.0-only
"""Custom-field definitions (API) and payload validation (the service modules call)."""

import uuid
from typing import Any

import pytest

from pickwise.platform.custom_fields import service
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database
from pickwise.shared.errors import UnprocessableError
from tests.integration.conftest import MakeApi
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db


def field_body(key: str, field_type: str = "text", **extra: Any) -> dict[str, Any]:
    return {
        "entity_type": "employee",
        "key": key,
        "label": key.replace("_", " ").title(),
        "field_type": field_type,
        **extra,
    }


async def admin_api(make_api: MakeApi, tenant: Tenant) -> Api:
    return await (await make_api()).sign_in(tenant.admin)


# --- definitions API -------------------------------------------------------------------------


async def test_definition_lifecycle(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_api(make_api, tenant)
    created = await admin.post(
        "/v1/custom-fields",
        field_body(
            "t_shirt",
            "select",
            options={"choices": [{"value": "m", "label": "M"}, {"value": "l", "label": "L"}]},
        ),
    )
    assert created.status_code == 201, created.text
    field = created.json()

    assert (await admin.post("/v1/custom-fields", field_body("t_shirt"))).status_code == 409

    updated = await admin.put(
        f"/v1/custom-fields/{field['id']}",
        {
            "label": "Shirt size",
            "options": field["options"],
            "required": True,
            "is_sensitive": False,
            "position": 3,
            "row_version": field["row_version"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["label"] == "Shirt size"
    assert updated.json()["key"] == "t_shirt"  # fixed
    stale = await admin.put(
        f"/v1/custom-fields/{field['id']}",
        {"label": "x", "options": field["options"], "row_version": field["row_version"]},
    )
    assert stale.status_code == 409

    archived = await admin.post(f"/v1/custom-fields/{field['id']}/archive")
    assert archived.json()["archived_at"] is not None
    listed = (await admin.get("/v1/custom-fields", params={"entity_type": "employee"})).json()
    assert [f["key"] for f in listed] == []
    with_archived = (
        await admin.get("/v1/custom-fields", params={"include_archived": "true"})
    ).json()
    assert [f["key"] for f in with_archived] == ["t_shirt"]
    # The key is free again while archived, and unarchiving then conflicts.
    assert (await admin.post("/v1/custom-fields", field_body("t_shirt"))).status_code == 201
    assert (await admin.post(f"/v1/custom-fields/{field['id']}/unarchive")).status_code == 409


@pytest.mark.parametrize(
    ("field_type", "options"),
    [
        ("select", {}),
        ("select", {"choices": []}),
        ("select", {"choices": [{"value": "a", "label": "A"}, {"value": "a", "label": "B"}]}),
        ("multiselect", {"choices": [{"value": "a"}]}),
        ("text", {"max_length": 0}),
        ("text", {"choices": []}),
        ("number", {"min": 5, "max": 1}),
        ("number", {"integer": "yes"}),
        ("date", {"min": "tomorrow"}),
        ("boolean", {"min": 1}),
    ],
)
async def test_options_are_validated_per_type(
    make_api: MakeApi, tenant: Tenant, field_type: str, options: dict[str, Any]
) -> None:
    admin = await admin_api(make_api, tenant)
    response = await admin.post(
        "/v1/custom-fields", field_body("opt_test", field_type, options=options)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_options"


async def test_who_may_read_and_manage(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    admin = await admin_api(make_api, tenant)
    await admin.post("/v1/custom-fields", field_body("public_field"))
    await admin.post("/v1/custom-fields", field_body("secret_field", is_sensitive=True))
    employee = await (await make_api()).sign_in(await env.add_account(tenant, "employee"))

    visible = {f["key"] for f in (await employee.get("/v1/custom-fields")).json()}
    assert visible == {"public_field"}  # sensitive definitions stay hidden
    assert (await employee.post("/v1/custom-fields", field_body("x_field"))).status_code == 403
    all_keys = {f["key"] for f in (await admin.get("/v1/custom-fields")).json()}
    assert all_keys == {"public_field", "secret_field"}


async def test_definitions_are_per_tenant(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    other = await env.create_tenant()
    await (await admin_api(make_api, tenant)).post("/v1/custom-fields", field_body("only_mine"))
    theirs = await admin_api(make_api, other)
    assert (await theirs.get("/v1/custom-fields")).json() == []
    assert (await theirs.post("/v1/custom-fields", field_body("only_mine"))).status_code == 201


# --- payload validation ----------------------------------------------------------------------


def ctx(tenant: Tenant) -> RequestContext:
    return RequestContext(ActorType.USER, tenant.id, tenant.admin.user_id)


async def define(admin: Api, key: str, field_type: str = "text", **extra: Any) -> dict[str, Any]:
    response = await admin.post("/v1/custom-fields", field_body(key, field_type, **extra))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def validate(
    db: Database, tenant: Tenant, payload: dict[str, Any], **kwargs: Any
) -> dict[str, Any]:
    async with db.tenant_session(ctx(tenant)) as session:
        return await service.validate_custom_fields(session, "employee", payload, **kwargs)


async def rejected(
    db: Database, tenant: Tenant, payload: dict[str, Any], **kwargs: Any
) -> dict[str, str]:
    with pytest.raises(UnprocessableError) as caught:
        await validate(db, tenant, payload, **kwargs)
    assert caught.value.code == "invalid_custom_fields"
    fields: dict[str, str] = caught.value.details["fields"]
    return fields


async def test_scalar_types_are_checked_and_normalised(
    make_api: MakeApi, api_db: Database, tenant: Tenant
) -> None:
    admin = await admin_api(make_api, tenant)
    await define(admin, "nickname", options={"max_length": 5})
    await define(admin, "age", "number", options={"min": 18, "max": 70, "integer": True})
    await define(admin, "joined", "date", options={"min": "2020-01-01"})
    await define(admin, "remote", "boolean")
    await define(
        admin,
        "size",
        "select",
        options={"choices": [{"value": "m", "label": "M"}, {"value": "l", "label": "L"}]},
    )
    await define(
        admin,
        "langs",
        "multiselect",
        options={
            "choices": [{"value": "en", "label": "English"}, {"value": "hi", "label": "Hindi"}]
        },
    )

    ok = await validate(
        api_db,
        tenant,
        {
            "nickname": "  Ash ",
            "age": 30,
            "joined": "2023-04-05",
            "remote": False,
            "size": "m",
            "langs": ["en", "hi"],
        },
    )
    assert ok["nickname"] == "Ash"
    assert ok["joined"] == "2023-04-05"

    errors = await rejected(
        api_db,
        tenant,
        {
            "nickname": "too long",
            "age": 17.5,
            "joined": "2019-12-31",
            "remote": "yes",
            "size": "xl",
            "langs": ["en", "en"],
        },
    )
    assert set(errors) == {"nickname", "age", "joined", "remote", "size", "langs"}  # all at once
    assert (await rejected(api_db, tenant, {"age": True}))["age"] == "must be a number"
    assert "whole number" in (await rejected(api_db, tenant, {"age": 20.5}))["age"]
    assert (await rejected(api_db, tenant, {"joined": "05/04/2023"}))["joined"].startswith(
        "must be a date"
    )


async def test_unknown_keys_and_required_fields(
    make_api: MakeApi, api_db: Database, tenant: Tenant
) -> None:
    admin = await admin_api(make_api, tenant)
    await define(admin, "badge_no", required=True)
    await define(admin, "hobby")

    assert (await rejected(api_db, tenant, {"hobby": "chess", "mystery": 1}))["mystery"] == (
        "isn't a known field"
    )
    errors = await rejected(api_db, tenant, {"hobby": "chess"})
    assert errors == {"badge_no": "is required"}
    assert await validate(api_db, tenant, {"badge_no": "B-7"}) == {"badge_no": "B-7"}
    # None clears an optional field and is refused for a required one.
    assert await validate(api_db, tenant, {"badge_no": "B-7", "hobby": None}) == {"badge_no": "B-7"}
    assert await rejected(api_db, tenant, {"badge_no": None}) == {"badge_no": "is required"}


async def test_archived_fields_keep_their_values_but_refuse_changes(
    make_api: MakeApi, api_db: Database, tenant: Tenant
) -> None:
    admin = await admin_api(make_api, tenant)
    field = await define(admin, "old_code")
    await admin.post(f"/v1/custom-fields/{field['id']}/archive")

    stored = {"old_code": "X1"}
    assert await validate(api_db, tenant, {"old_code": "X1"}, existing=stored) == stored
    errors = await rejected(api_db, tenant, {"old_code": "X2"}, existing=stored)
    assert errors["old_code"] == "is archived and can't be changed"
    assert "old_code" in await rejected(api_db, tenant, {"old_code": "new"})


async def test_sensitive_fields_are_protected(
    make_api: MakeApi, api_db: Database, tenant: Tenant
) -> None:
    admin = await admin_api(make_api, tenant)
    await define(admin, "salary_band", is_sensitive=True)
    await define(admin, "desk")

    assert "salary_band" in await rejected(api_db, tenant, {"salary_band": "B2"})
    assert await validate(api_db, tenant, {"salary_band": "B2"}, can_edit_sensitive=True) == {
        "salary_band": "B2"
    }
    existing = {"salary_band": "B2"}
    # Someone who can't see the field edits another one: the hidden value survives.
    kept = await validate(api_db, tenant, {"desk": "4F"}, existing=existing)
    assert kept == {"desk": "4F", "salary_band": "B2"}
    assert "salary_band" in await rejected(api_db, tenant, {"salary_band": "B9"}, existing=existing)

    async with api_db.tenant_session(ctx(tenant)) as session:
        payload = {"salary_band": "B2", "desk": "4F"}
        shown = await service.visible_custom_fields(
            session, "employee", payload, can_read_sensitive=False
        )
        everything = await service.visible_custom_fields(
            session, "employee", payload, can_read_sensitive=True
        )
    assert shown == {"desk": "4F"}
    assert everything == payload


async def test_user_and_file_references_must_belong_to_the_tenant(
    make_api: MakeApi, env: Env, api_db: Database, tenant: Tenant
) -> None:
    admin = await admin_api(make_api, tenant)
    await define(admin, "buddy", "user")
    await define(admin, "contract", "file")
    member = await env.add_account(tenant, "employee")
    other = await env.create_tenant()
    stranger = other.admin

    assert await validate(api_db, tenant, {"buddy": str(member.user_id)}) == {
        "buddy": str(member.user_id)
    }
    assert "buddy" in await rejected(api_db, tenant, {"buddy": str(stranger.user_id)})
    assert "buddy" in await rejected(api_db, tenant, {"buddy": "not-a-uuid"})
    assert "buddy" in await rejected(api_db, tenant, {"buddy": str(uuid.uuid4())})

    async def file_in(owner: Tenant, status: str) -> uuid.UUID:
        ((file_id,),) = await env.sql(
            "INSERT INTO platform.files (tenant_id, storage_key, bucket, original_name, "
            "scan_status) VALUES (:t, :k, 'b', 'f.pdf', :s) RETURNING id",
            t=owner.id,
            k=f"{owner.id}/{uuid.uuid4()}",
            s=status,
        )
        return uuid.UUID(str(file_id))

    clean, pending = await file_in(tenant, "clean"), await file_in(tenant, "pending")
    infected, foreign = await file_in(tenant, "infected"), await file_in(other, "clean")
    assert await validate(api_db, tenant, {"contract": str(clean)})
    assert await validate(api_db, tenant, {"contract": str(pending)})  # attachable while scanning
    assert "contract" in await rejected(api_db, tenant, {"contract": str(infected)})
    assert "contract" in await rejected(api_db, tenant, {"contract": str(foreign)})
