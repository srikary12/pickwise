# SPDX-License-Identifier: AGPL-3.0-only
"""Tenant administration: members, roles, assignments, API keys, idempotency."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pickwise.platform.rbac.system_roles import default_grants
from tests.integration.conftest import MakeApi
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db


async def admin_of(make_api: MakeApi, tenant: Tenant) -> Api:
    return await (await make_api()).sign_in(tenant.admin)


def invite_body(tenant: Tenant, role: str = "employee") -> dict[str, str]:
    return {
        "email": f"p-{uuid.uuid4().hex[:8]}@{tenant.slug}.test",
        "display_name": "Some Person",
        "role_key": role,
    }


# --- members -----------------------------------------------------------------------------


async def test_member_list_shows_roles(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    members = (await admin.get("/v1/admin/users")).json()
    me = next(m for m in members if m["email"] == tenant.admin.email)
    assert me["status"] == "active"
    assert [r["role_key"] for r in me["roles"]] == ["tenant_admin"]


async def test_cannot_invite_an_unknown_role_or_escalate(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    hr = await (await make_api()).sign_in(await env.add_account(tenant, "hr_admin"))
    unknown = await hr.post("/v1/admin/users/invite", invite_body(tenant, "no_such_role"))
    assert unknown.status_code == 422
    # HR admins hold fewer permissions than tenant_admin, so they can't hand that role out.
    escalate = await hr.post("/v1/admin/users/invite", invite_body(tenant, "tenant_admin"))
    assert escalate.status_code in (403, 422)
    assert (
        await hr.post("/v1/admin/users/invite", invite_body(tenant, "employee"))
    ).status_code == 201


async def test_inviting_an_active_member_conflicts(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    existing = await env.add_account(tenant, "employee")
    response = await admin.post(
        "/v1/admin/users/invite",
        {"email": existing.email, "display_name": "Dup", "role_key": "employee"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_member"


async def test_suspend_revokes_their_access_and_row_version_guards(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    victim = await env.add_account(tenant, "hr_admin")
    theirs = await (await make_api()).sign_in(victim)
    assert (await theirs.get("/v1/admin/users")).status_code == 200

    member = next(
        m for m in (await admin.get("/v1/admin/users")).json() if m["email"] == victim.email
    )
    stale = await admin.patch(
        f"/v1/admin/users/{member['membership_id']}",
        {"status": "suspended", "row_version": member["row_version"] + 7},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_row_version"

    done = await admin.patch(
        f"/v1/admin/users/{member['membership_id']}",
        {"status": "suspended", "row_version": member["row_version"]},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "suspended"
    assert (await theirs.get("/v1/admin/users")).status_code == 401  # session revoked
    assert (await (await make_api()).login(victim.email)).json()["stage"] == "tenant_selection"


async def test_cannot_change_your_own_membership(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    response = await admin.patch(
        f"/v1/admin/users/{tenant.admin.membership_id}",
        {"status": "suspended", "row_version": 1},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "self_change"


async def test_unknown_membership_is_404(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    response = await admin.patch(
        f"/v1/admin/users/{uuid.uuid4()}", {"status": "active", "row_version": 1}
    )
    assert response.status_code == 404


# --- roles and assignments -----------------------------------------------------------------


async def test_custom_role_lifecycle(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    created = await admin.post(
        "/v1/admin/roles",
        {"key": "user_viewer", "name": "User viewer", "permissions": ["platform.users.read"]},
    )
    assert created.status_code == 201, created.text
    role = created.json()
    assert role["is_system"] is False
    assert role["permissions"] == ["platform.users.read"]
    dup = await admin.post("/v1/admin/roles", {"key": "user_viewer", "name": "Again"})
    assert dup.status_code == 409

    person = await env.add_account(tenant, "employee")
    member = next(
        m for m in (await admin.get("/v1/admin/users")).json() if m["email"] == person.email
    )
    theirs = await (await make_api()).sign_in(person)
    assert (await theirs.get("/v1/admin/users")).status_code == 403

    grant = await admin.post(
        "/v1/admin/role-assignments",
        {"membership_id": member["membership_id"], "role_id": role["id"]},
    )
    assert grant.status_code == 201, grant.text
    # Role changes apply on the very next request.
    assert (await theirs.get("/v1/admin/users")).status_code == 200
    assert (await theirs.get("/v1/admin/roles")).status_code == 403

    updated = await admin.put(
        f"/v1/admin/roles/{role['id']}/permissions",
        {
            "permissions": ["platform.users.read", "platform.roles.read"],
            "row_version": role["row_version"],
        },
    )
    assert updated.status_code == 200
    assert (await theirs.get("/v1/admin/roles")).status_code == 200
    stale = await admin.put(
        f"/v1/admin/roles/{role['id']}/permissions",
        {"permissions": [], "row_version": role["row_version"]},
    )
    assert stale.status_code == 409

    assert (
        await admin.delete(f"/v1/admin/role-assignments/{grant.json()['id']}")
    ).status_code == 204
    assert (await theirs.get("/v1/admin/users")).status_code == 403


async def test_system_roles_cannot_be_edited(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    employee = next(
        r for r in (await admin.get("/v1/admin/roles")).json() if r["key"] == "employee"
    )
    response = await admin.put(
        f"/v1/admin/roles/{employee['id']}/permissions",
        {"permissions": ["platform.users.read"], "row_version": employee["row_version"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "system_role"


async def test_unknown_permissions_are_rejected(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    response = await admin.post(
        "/v1/admin/roles", {"key": "bad_role", "name": "Bad", "permissions": ["nope.nothing"]}
    )
    assert response.status_code == 422


async def test_cannot_grant_a_role_with_permissions_you_lack(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    # A custom role that may manage roles but holds nothing else.
    role = (
        await admin.post(
            "/v1/admin/roles",
            {
                "key": "role_manager",
                "name": "Role manager",
                "permissions": ["platform.roles.manage"],
            },
        )
    ).json()
    person = await env.add_account(tenant, "employee")
    ally = await env.add_account(tenant, "employee")
    members = {m["email"]: m for m in (await admin.get("/v1/admin/users")).json()}
    await admin.post(
        "/v1/admin/role-assignments",
        {"membership_id": members[person.email]["membership_id"], "role_id": role["id"]},
    )
    theirs = await (await make_api()).sign_in(person)
    tenant_admin_role = next(
        r for r in (await admin.get("/v1/admin/roles")).json() if r["key"] == "tenant_admin"
    )
    escalate = await theirs.post(
        "/v1/admin/role-assignments",
        {"membership_id": members[ally.email]["membership_id"], "role_id": tenant_admin_role["id"]},
    )
    assert escalate.status_code in (403, 422)
    assert escalate.status_code != 201


async def test_entity_scopes_need_a_scope_id(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    person = await env.add_account(tenant, "employee")
    members = {m["email"]: m for m in (await admin.get("/v1/admin/users")).json()}
    role = next(r for r in (await admin.get("/v1/admin/roles")).json() if r["key"] == "employee")
    response = await admin.post(
        "/v1/admin/role-assignments",
        {
            "membership_id": members[person.email]["membership_id"],
            "role_id": role["id"],
            "scope_type": "department",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_scope"


async def test_the_last_tenant_admin_cannot_be_removed(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    me = next(
        m for m in (await admin.get("/v1/admin/users")).json() if m["email"] == tenant.admin.email
    )
    response = await admin.delete(f"/v1/admin/role-assignments/{me['roles'][0]['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_admin"


async def test_permission_catalog(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    codes = {p["code"] for p in (await admin.get("/v1/permissions")).json()}
    assert {"platform.users.read", "platform.api_keys.manage"} <= codes


# --- idempotency -----------------------------------------------------------------------------


async def test_idempotent_invite_replays_without_a_second_email(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    body = invite_body(tenant)
    headers = {"Idempotency-Key": f"inv-{uuid.uuid4().hex}"}
    first = await admin.post("/v1/admin/users/invite", body, headers=headers)
    second = await admin.post("/v1/admin/users/invite", body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    await env.mailbox.deliver()
    assert env.mailbox.count_to(body["email"]) == 1


async def test_idempotency_key_with_a_different_body_is_rejected(
    make_api: MakeApi, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    headers = {"Idempotency-Key": f"inv-{uuid.uuid4().hex}"}
    await admin.post("/v1/admin/users/invite", invite_body(tenant), headers=headers)
    other = await admin.post("/v1/admin/users/invite", invite_body(tenant), headers=headers)
    assert other.status_code == 422
    assert other.json()["error"]["code"] == "idempotency_mismatch"


async def test_idempotency_keys_are_scoped_to_the_caller(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    other_admin = await env.add_account(tenant, "tenant_admin")
    second = await (await make_api()).sign_in(other_admin)
    key = {"Idempotency-Key": f"shared-{uuid.uuid4().hex}"}
    a = await admin.post("/v1/admin/users/invite", invite_body(tenant), headers=key)
    b = await second.post("/v1/admin/users/invite", invite_body(tenant), headers=key)
    assert a.status_code == b.status_code == 201
    assert a.json()["email"] != b.json()["email"]


async def test_invalid_idempotency_key_is_rejected(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    response = await admin.post(
        "/v1/admin/users/invite", invite_body(tenant), headers={"Idempotency-Key": "x" * 300}
    )
    assert response.status_code == 422


async def test_expired_idempotency_key_starts_over(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    key = f"old-{uuid.uuid4().hex}"
    body = {"key": f"r_{uuid.uuid4().hex[:8]}", "name": "R", "permissions": []}
    first = await admin.post("/v1/admin/roles", body, headers={"Idempotency-Key": key})
    assert first.status_code == 201
    await env.sql(
        "UPDATE platform.idempotency_keys SET expires_at = now() - interval '1 second' "
        "WHERE key = :k",
        k=key,
    )
    again = await admin.post("/v1/admin/roles", body, headers={"Idempotency-Key": key})
    assert again.status_code == 409  # re-ran for real: the role already exists


# --- API keys -------------------------------------------------------------------------------


async def make_key(admin: Api, scopes: list[str], **extra: object) -> dict[str, object]:
    response = await admin.post(
        "/v1/admin/api-keys", {"name": "integration", "scopes": scopes, **extra}
    )
    assert response.status_code == 201, response.text
    out: dict[str, object] = response.json()
    return out


def bearer(key: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_api_key_works_only_within_its_scopes(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    created = await make_key(admin, ["platform.users.read"])
    key = str(created["key"])
    assert key.startswith(f"pw_{created['prefix']}_")
    client = await make_api()
    allowed = await client.get("/v1/admin/users", headers=bearer(key))
    assert allowed.status_code == 200
    forbidden = await client.get("/v1/admin/roles", headers=bearer(key))
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "permission_denied"
    # Not a session: session-only endpoints refuse it.
    assert (await client.get("/v1/me", headers=bearer(key))).status_code == 401


async def test_api_key_is_stored_hashed_and_listed_without_the_secret(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    created = await make_key(admin, ["platform.users.read"])
    secret = str(created["key"]).rsplit("_", 1)[-1]
    ((count,),) = await env.sql(
        "SELECT count(*) FROM platform.api_keys WHERE id = :i AND position(:s in encode(key_hash, 'hex')) > 0",
        i=created["id"],
        s=secret,
    )
    assert count == 0
    listed = (await admin.get("/v1/admin/api-keys")).json()
    assert all("key" not in k or k.get("key") is None for k in listed)


async def test_api_key_post_requires_no_csrf_token(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    # Inviting an employee needs everything the employee role holds (no escalation).
    scopes = ["platform.users.invite", *default_grants("employee")]
    key = str((await make_key(admin, scopes))["key"])
    bare = await make_api()
    bare.http.headers.pop("X-CSRF-Token")
    response = await bare.post("/v1/admin/users/invite", invite_body(tenant), headers=bearer(key))
    assert response.status_code == 201


async def test_revoked_key_stops_working(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    created = await make_key(admin, ["platform.users.read"])
    client = await make_api()
    assert (await client.get("/v1/admin/users", headers=bearer(created["key"]))).status_code == 200
    assert (await admin.delete(f"/v1/admin/api-keys/{created['id']}")).status_code == 204
    revoked = await client.get("/v1/admin/users", headers=bearer(created["key"]))
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "invalid_api_key"
    assert (await admin.delete(f"/v1/admin/api-keys/{created['id']}")).status_code == 404


async def test_expired_key_stops_working(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    created = await make_key(admin, ["platform.users.read"], expires_at=soon)
    client = await make_api()
    assert (await client.get("/v1/admin/users", headers=bearer(created["key"]))).status_code == 200
    await env.sql(
        "UPDATE platform.api_keys SET expires_at = now() - interval '1 second' WHERE id = :i",
        i=created["id"],
    )
    assert (await client.get("/v1/admin/users", headers=bearer(created["key"]))).status_code == 401


async def test_garbage_and_unknown_keys_are_refused(make_api: MakeApi) -> None:
    client = await make_api()
    for token in ("nonsense", "pw_deadbeef_" + "x" * 43, ""):
        response = await client.get("/v1/admin/users", headers=bearer(token))
        assert response.status_code == 401


async def test_api_key_is_confined_to_its_own_tenant(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    other = await env.create_tenant()
    key = (await make_key(await admin_of(make_api, tenant), ["platform.users.read"]))["key"]
    client = await make_api()
    emails = {m["email"] for m in (await client.get("/v1/admin/users", headers=bearer(key))).json()}
    assert tenant.admin.email in emails
    assert other.admin.email not in emails


async def test_cannot_mint_a_key_with_more_than_you_hold(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    role = (
        await admin.post(
            "/v1/admin/roles",
            {"key": "key_maker", "name": "Key maker", "permissions": ["platform.api_keys.manage"]},
        )
    ).json()
    person = await env.add_account(tenant, "employee")
    members = {m["email"]: m for m in (await admin.get("/v1/admin/users")).json()}
    await admin.post(
        "/v1/admin/role-assignments",
        {"membership_id": members[person.email]["membership_id"], "role_id": role["id"]},
    )
    theirs = await (await make_api()).sign_in(person)
    response = await theirs.post(
        "/v1/admin/api-keys", {"name": "greedy", "scopes": ["platform.roles.manage"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "permission_denied"
    unknown = await theirs.post(
        "/v1/admin/api-keys", {"name": "bad", "scopes": ["not.a.permission"]}
    )
    assert unknown.status_code == 422


async def test_idempotent_key_creation_never_replays_the_secret(
    make_api: MakeApi, tenant: Tenant
) -> None:
    admin = await admin_of(make_api, tenant)
    headers = {"Idempotency-Key": f"key-{uuid.uuid4().hex}"}
    body = {"name": "once", "scopes": ["platform.users.read"]}
    first = await admin.post("/v1/admin/api-keys", body, headers=headers)
    second = await admin.post("/v1/admin/api-keys", body, headers=headers)
    assert first.json()["key"].startswith("pw_")
    assert second.status_code == 201
    assert second.json()["key"] is None
    assert second.json()["id"] == first.json()["id"]


# --- SSO configuration -------------------------------------------------------------------------


async def test_sso_config_crud_never_returns_the_secret(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    body = {
        "issuer": "https://login.example.test",
        "client_id": "abc",
        "client_secret": "super-secret-value",
        "allowed_domains": [f"{tenant.slug}.test"],
        "jit_provisioning": True,
        "default_role_key": "employee",
    }
    created = await admin.post("/v1/admin/sso-configs", body)
    assert created.status_code == 201, created.text
    assert "super-secret-value" not in created.text
    config = created.json()
    listed = await admin.get("/v1/admin/sso-configs")
    assert "super-secret-value" not in listed.text
    assert len(listed.json()) == 1

    changed = await admin.put(
        f"/v1/admin/sso-configs/{config['id']}",
        {**body, "client_secret": None, "enforce_sso": True, "row_version": config["row_version"]},
    )
    assert changed.status_code == 200
    assert changed.json()["enforce_sso"] is True
    http_issuer = await admin.post(
        "/v1/admin/sso-configs", {**body, "issuer": "http://insecure.test"}
    )
    assert http_issuer.status_code == 422
    assert (await admin.delete(f"/v1/admin/sso-configs/{config['id']}")).status_code == 204
    assert (await admin.get("/v1/admin/sso-configs")).json() == []


# --- audit -------------------------------------------------------------------------------------


async def test_role_grants_are_audited(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    admin = await admin_of(make_api, tenant)
    person = await env.add_account(tenant, "employee")
    members = {m["email"]: m for m in (await admin.get("/v1/admin/users")).json()}
    role = next(r for r in (await admin.get("/v1/admin/roles")).json() if r["key"] == "hr_ops")
    response = await admin.post(
        "/v1/admin/role-assignments",
        {"membership_id": members[person.email]["membership_id"], "role_id": role["id"]},
    )
    ((count,),) = await env.sql(
        "SELECT count(*) FROM audit.events WHERE tenant_id = :t AND action = 'role.grant' "
        "AND entity_id = :e",
        t=tenant.id,
        e=uuid.UUID(response.json()["id"]),
    )
    assert count == 1
