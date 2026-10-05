# SPDX-License-Identifier: AGPL-3.0-only
"""The tenant logo: who may set it, what is accepted, and who sees it."""

import uuid

import pytest

from tests.integration.conftest import MakeApi
from tests.integration.support import Api, Env, Tenant
from tests.integration.test_files import PDF, PDF_TYPE, PNG, fetch, upload

pytestmark = pytest.mark.db

LOGO = {"owner_entity_type": "tenant_logo"}


async def logo_upload(api: Api, env: Env, tenant: Tenant, **overrides: object) -> dict[str, str]:
    args: dict[str, object] = {
        "content": PNG,
        "name": "logo.png",
        "mime": "image/png",
        "classification": "public",
        "owner_entity_id": str(tenant.id),
        **LOGO,
        **overrides,
    }
    file = await upload(api, env, **args)  # type: ignore[arg-type]
    await env.scan_file(tenant.id, uuid.UUID(file["id"]))
    return {"id": file["id"]}


async def row_version(api: Api) -> int:
    return int((await api.get("/v1/admin/tenant")).json()["row_version"])


async def test_set_show_and_remove_the_logo(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    member = await (await make_api()).sign_in(await env.add_account(tenant, "employee"))
    assert (await admin.get("/v1/branding")).json()["logo_version"] is None
    assert (await admin.get("/v1/branding/logo")).status_code == 404

    file = await logo_upload(admin, env, tenant)
    set_ = await admin.put(
        "/v1/admin/tenant/logo", {"file_id": file["id"], "row_version": await row_version(admin)}
    )
    assert set_.status_code == 200, set_.text
    assert set_.json()["logo_version"] == file["id"]

    # Every member sees it: in /v1/me, and as an image behind a short-lived redirect.
    assert (await member.get("/v1/me")).json()["logo_version"] == file["id"]
    assert (await member.get("/v1/branding")).json()["name"] == set_.json()["name"]
    redirect = await member.get("/v1/branding/logo", params={"v": file["id"]})
    assert redirect.status_code == 302
    image = await fetch(redirect.headers["location"])
    assert image.status_code == 200
    assert image.content == PNG
    # …and may download it through the file API too.
    assert (await member.get(f"/v1/files/{file['id']}/download")).status_code == 200

    removed = await admin.delete(
        "/v1/admin/tenant/logo", params={"row_version": await row_version(admin)}
    )
    assert removed.status_code == 200
    assert removed.json()["logo_version"] is None
    assert (await member.get("/v1/me")).json()["logo_version"] is None


async def test_only_tenant_managers_change_the_logo(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    member_account = await env.add_account(tenant, "employee")
    member = await (await make_api()).sign_in(member_account)
    admin = await (await make_api()).sign_in(tenant.admin)
    file = await logo_upload(admin, env, tenant)
    body = {"file_id": file["id"], "row_version": 1}
    assert (await member.put("/v1/admin/tenant/logo", body)).status_code == 403
    assert (
        await member.delete("/v1/admin/tenant/logo", params={"row_version": 1})
    ).status_code == 403


async def test_the_file_must_be_a_clean_png_or_jpeg_uploaded_as_a_logo(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    version = await row_version(admin)

    async def put(file_id: str) -> int:
        response = await admin.put(
            "/v1/admin/tenant/logo", {"file_id": file_id, "row_version": version}
        )
        return response.status_code

    pdf = await logo_upload(admin, env, tenant, content=PDF, name="x.pdf", mime=PDF_TYPE)
    assert await put(pdf["id"]) == 422  # not an image

    plain = await upload(admin, env, PNG, "p.png", "image/png")  # no logo owner type
    await env.scan_file(tenant.id, uuid.UUID(plain["id"]))
    assert await put(plain["id"]) == 422

    pending = await upload(
        admin, env, PNG, "q.png", "image/png", owner_entity_id=str(tenant.id), **LOGO
    )
    assert await put(pending["id"]) == 409  # still being scanned

    assert await put(str(uuid.uuid4())) == 404


async def test_someone_elses_upload_cannot_be_used(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    other_admin = await env.add_account(tenant, "tenant_admin")
    other = await (await make_api()).sign_in(other_admin)
    admin = await (await make_api()).sign_in(tenant.admin)
    file = await logo_upload(other, env, tenant)
    response = await admin.put(
        "/v1/admin/tenant/logo", {"file_id": file["id"], "row_version": await row_version(admin)}
    )
    assert response.status_code == 404


async def test_a_stale_row_version_is_a_conflict(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    file = await logo_upload(admin, env, tenant)
    version = await row_version(admin)
    ok = await admin.put("/v1/admin/tenant/logo", {"file_id": file["id"], "row_version": version})
    assert ok.status_code == 200
    stale = await admin.put(
        "/v1/admin/tenant/logo", {"file_id": file["id"], "row_version": version}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_row_version"


async def test_another_tenants_logo_is_invisible(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    other = await env.create_tenant()
    other_admin = await (await make_api()).sign_in(other.admin)
    file = await logo_upload(other_admin, env, other)
    await other_admin.put(
        "/v1/admin/tenant/logo",
        {"file_id": file["id"], "row_version": await row_version(other_admin)},
    )
    mine = await (await make_api()).sign_in(tenant.admin)
    assert (await mine.get("/v1/me")).json()["logo_version"] is None
    assert (await mine.get(f"/v1/files/{file['id']}/download")).status_code == 404
