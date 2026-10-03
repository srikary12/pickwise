# SPDX-License-Identifier: AGPL-3.0-only
"""Tenant provisioning, default sync, self-serve signup and the email outbox."""

import os
import uuid

import httpx
import pytest
from fastapi import FastAPI

from pickwise.platform.notifications import email as email_module
from pickwise.platform.notifications.email import QueuedEmail, due_emails
from pickwise.platform.notifications.email import _smtp_send as real_smtp_send
from pickwise.platform.permissions import PERMISSIONS
from pickwise.platform.provisioning import PROVISIONING_HOOKS, signup
from pickwise.platform.provisioning.service import (
    ProvisioningError,
    provision_tenant,
    sync_defaults,
)
from pickwise.platform.rbac.system_roles import SYSTEM_ROLES, default_grants
from pickwise.shared.db import ops_task
from pickwise.shared.settings import Settings
from tests.integration.conftest import MakeApi
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db


async def counts(env: Env, tenant_id: uuid.UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, sql in {
        "roles": "SELECT count(*) FROM platform.roles WHERE tenant_id = :t",
        "grants": "SELECT count(*) FROM platform.role_permissions WHERE tenant_id = :t",
        "sequences": "SELECT count(*) FROM platform.number_sequences WHERE tenant_id = :t",
        "keys": "SELECT count(*) FROM platform.tenant_keys WHERE tenant_id = :t",
    }.items():
        out[name] = (await env.sql(sql, t=tenant_id))[0][0]
    return out


async def test_provisioning_creates_keys_roles_grants_and_sequences(
    env: Env, tenant: Tenant
) -> None:
    c = await counts(env, tenant.id)
    assert c["roles"] == len(SYSTEM_ROLES)
    assert c["keys"] == 2  # data + blind index
    assert c["grants"] == sum(len(default_grants(r.key)) for r in SYSTEM_ROLES)
    assert c["sequences"] >= 1
    ((status, demo),) = await env.sql(
        "SELECT status, coalesce(settings ->> 'demo', 'no') FROM platform.tenants WHERE id = :t",
        t=tenant.id,
    )
    assert status == "active"
    assert demo == "no"


async def test_tenant_admin_holds_every_permission(env: Env, tenant: Tenant) -> None:
    rows = await env.sql(
        "SELECT rp.permission_code FROM platform.role_permissions rp "
        "JOIN platform.roles r ON r.tenant_id = rp.tenant_id AND r.id = rp.role_id "
        "WHERE rp.tenant_id = :t AND r.key = 'tenant_admin'",
        t=tenant.id,
    )
    assert {r[0] for r in rows} == {p.code for p in PERMISSIONS.all()}


async def test_running_the_hooks_again_changes_nothing(env: Env, tenant: Tenant) -> None:
    before = await counts(env, tenant.id)

    @ops_task
    async def rerun() -> None:
        async with env.db.ops_session() as s:
            await s.execute(
                __import__("sqlalchemy").text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenant.id)},
            )
            await PROVISIONING_HOOKS.run_all(s, tenant.id)

    await rerun()
    await rerun()
    assert await counts(env, tenant.id) == before


async def test_sync_defaults_backfills_missing_grants_and_roles(env: Env, tenant: Tenant) -> None:
    before = await counts(env, tenant.id)
    await env.sql(
        "DELETE FROM platform.role_permissions WHERE tenant_id = :t AND permission_code = "
        "'platform.users.read'",
        t=tenant.id,
    )
    await env.sql(
        "DELETE FROM platform.role_assignments ra USING platform.roles r WHERE ra.tenant_id = :t "
        "AND r.tenant_id = ra.tenant_id AND r.id = ra.role_id AND r.key = 'interviewer'",
        t=tenant.id,
    )
    await env.sql(
        "DELETE FROM platform.roles WHERE tenant_id = :t AND key = 'interviewer'", t=tenant.id
    )
    assert (await counts(env, tenant.id))["grants"] < before["grants"]

    @ops_task
    async def run() -> int:
        async with env.db.ops_session() as s:
            return len(await sync_defaults(s))

    assert await run() >= 1
    assert await counts(env, tenant.id) == before


async def test_custom_roles_survive_a_default_sync(env: Env, tenant: Tenant) -> None:
    await env.sql(
        "INSERT INTO platform.roles (tenant_id, key, name) VALUES (:t, 'custom_x', 'Custom')",
        t=tenant.id,
    )

    @ops_task
    async def run() -> None:
        async with env.db.ops_session() as s:
            await sync_defaults(s)

    await run()
    assert (
        await env.sql(
            "SELECT count(*) FROM platform.roles WHERE tenant_id = :t AND key = 'custom_x'",
            t=tenant.id,
        )
    )[0][0] == 1


@pytest.mark.parametrize("slug", ["ab", "Has Space", "UPPER_case!", "x" * 41])
async def test_provisioning_validates_the_slug(env: Env, slug: str) -> None:
    @ops_task
    async def attempt() -> None:
        async with env.db.ops_session() as s:
            await provision_tenant(
                s,
                env.settings,
                env.kek,
                slug=slug,
                name="X",
                admin_email="a@x.test",
                admin_name="A",
            )

    with pytest.raises(ProvisioningError):
        await attempt()


async def test_provisioning_refuses_a_taken_slug(env: Env, tenant: Tenant) -> None:
    @ops_task
    async def attempt() -> None:
        async with env.db.ops_session() as s:
            await provision_tenant(
                s,
                env.settings,
                env.kek,
                slug=tenant.slug,
                name="Dup",
                admin_email="d@x.test",
                admin_name="D",
            )

    with pytest.raises(ProvisioningError, match="already exists"):
        await attempt()


async def test_provisioned_invite_carries_its_secret_encrypted(env: Env) -> None:
    slug = f"inv-{uuid.uuid4().hex[:8]}"

    @ops_task
    async def create() -> uuid.UUID:
        async with env.db.ops_session() as s:
            result = await provision_tenant(
                s,
                env.settings,
                env.kek,
                slug=slug,
                name="Invite",
                admin_email=f"a@{slug}.test",
                admin_name="A",
            )
            assert result.invite_email is not None
            return result.tenant_id

    tenant_id = await create()
    env.tenant_ids.append(tenant_id)
    ((payload, enc, status),) = await env.sql(
        "SELECT payload::text, payload_enc, status FROM platform.email_outbox WHERE tenant_id = :t",
        t=tenant_id,
    )
    assert status == "queued"
    assert enc is not None
    assert "/invite/" not in payload  # the link token lives only in the ciphertext
    assert b"/invite/" not in bytes(enc)


# --- outbox ---------------------------------------------------------------------------------


async def queue_invite(make_api: MakeApi, tenant: Tenant) -> str:
    admin = await (await make_api()).sign_in(tenant.admin)
    address = f"mail-{uuid.uuid4().hex[:8]}@{tenant.slug}.test"
    response = await admin.post(
        "/v1/admin/users/invite", {"email": address, "display_name": "M", "role_key": "employee"}
    )
    assert response.status_code == 201
    return address


async def test_sending_nulls_the_secret_and_marks_sent(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    address = await queue_invite(make_api, tenant)
    await env.mailbox.deliver()
    ((status, enc, attempts),) = await env.sql(
        "SELECT status, payload_enc, attempts FROM platform.email_outbox WHERE to_address = :a",
        a=address,
    )
    assert (status, enc, attempts) == ("sent", None, 1)


async def test_failed_sends_retry_then_give_up_and_drop_the_secret(
    make_api: MakeApi, env: Env, tenant: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    address = await queue_invite(make_api, tenant)

    def boom(*_a: object) -> None:
        raise OSError("smtp down")

    monkeypatch.setattr(email_module, "_smtp_send", boom)
    pending, env.mailbox.pending = env.mailbox.pending, []
    mine = next(p for p in pending if p.tenant_id == tenant.id)
    env.mailbox.pending = [p for p in pending if p is not mine]
    for expected in ("queued", "queued", "queued", "queued", "failed"):
        assert await env.send(mine) == expected
    ((status, enc, error),) = await env.sql(
        "SELECT status, payload_enc, last_error FROM platform.email_outbox WHERE to_address = :a",
        a=address,
    )
    assert (status, enc, error) == ("failed", None, "OSError")
    assert await env.send(mine) == "skipped"


async def test_due_emails_only_returns_messages_waiting_over_a_minute(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    address = await queue_invite(make_api, tenant)
    ((outbox_id,),) = await env.sql(
        "SELECT id FROM platform.email_outbox WHERE to_address = :a", a=address
    )

    @ops_task
    async def find() -> list[uuid.UUID]:
        async with env.db.ops_session() as s:
            return [e.outbox_id for e in await due_emails(s)]

    assert outbox_id not in await find()  # just queued: the immediate send gets first go


@pytest.mark.skipif(not os.environ.get("MAILPIT_URL"), reason="needs Mailpit (make test)")
async def test_email_really_arrives_in_mailpit(
    make_api: MakeApi, env: Env, tenant: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(email_module, "_smtp_send", real_smtp_send)
    address = await queue_invite(make_api, tenant)
    mine = next(p for p in env.mailbox.pending if p.tenant_id == tenant.id)
    env.mailbox.pending.remove(mine)
    assert await env.send(mine) == "sent"
    async with httpx.AsyncClient(base_url=os.environ["MAILPIT_URL"]) as http:
        found = (await http.get("/api/v1/search", params={"query": f"to:{address}"})).json()
        assert found["total"] == 1
        message = (await http.get(f"/api/v1/message/{found['messages'][0]['ID']}")).json()
    assert "/invite/" in message["Text"]
    assert tenant.slug in message["Subject"] or "invit" in message["Subject"].lower()


# --- self-serve signup ----------------------------------------------------------------------


@pytest.fixture
def signup_on(app: FastAPI, api_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app.state, "settings", api_settings.model_copy(update={"signup_enabled": True})
    )


async def test_signup_is_hidden_when_disabled(api: Api, api_settings: Settings) -> None:
    assert api_settings.signup_enabled is False
    body = {"email": "founder@initech.test", "slug": "initech-x", "organisation_name": "Initech"}
    assert (await api.post("/v1/signup", body)).status_code == 404
    assert (await api.post("/v1/signup/verify", {"token": "x" * 30})).status_code == 404
    assert (await api.get(f"/v1/signup/{uuid.uuid4()}")).status_code == 404


async def test_signup_end_to_end(api: Api, env: Env, signup_on: None) -> None:
    slug = f"co-{uuid.uuid4().hex[:8]}"
    founder = f"founder@{slug}.test"
    started = await api.post(
        "/v1/signup", {"email": founder, "slug": slug, "organisation_name": "Initech"}
    )
    assert started.status_code == 202, started.text
    signup_id = uuid.UUID(started.json()["signup_id"])
    assert (await api.get(f"/v1/signup/{signup_id}")).json()["status"] == "pending_verification"

    token = await env.mailbox.token_for(founder, template_word="verify")
    verified = await api.post("/v1/signup/verify", {"token": token})
    assert verified.status_code == 202
    assert ("pickwise.provision_signup", {"signup_id": str(signup_id)}) in env.jobs
    assert (await api.get(f"/v1/signup/{signup_id}")).json()["status"] == "queued"
    again = await api.post("/v1/signup/verify", {"token": token})
    assert again.status_code == 400  # single use

    @ops_task
    async def worker() -> QueuedEmail | None:
        async with env.db.ops_session() as s:
            return await signup.provision(s, env.settings, env.kek, signup_id)

    invite = await worker()
    assert invite is not None
    await env.mailbox.dispatcher(invite)  # what the worker task does after commit
    assert await worker() is None  # idempotent: nothing left to do
    done = (await api.get(f"/v1/signup/{signup_id}")).json()
    assert done == {"signup_id": str(signup_id), "status": "completed", "tenant_slug": slug}
    ((tenant_id,),) = await env.sql("SELECT id FROM platform.tenants WHERE slug = :s", s=slug)
    env.tenant_ids.append(tenant_id)
    assert (await counts(env, tenant_id))["roles"] == len(SYSTEM_ROLES)
    # The founder is invited as the admin and can finish joining from the email.
    await env.mailbox.deliver()
    invite_token = await env.mailbox.token_for(founder, template_word="invit")
    joined = await api.post(
        "/v1/auth/invites/accept", {"token": invite_token, "password": "a founder passphrase"}
    )
    assert joined.status_code == 200
    assert joined.json()["active_tenant"]["slug"] == slug
    assert "platform.tenant.manage" in joined.json()["permissions"]


async def test_signup_refuses_a_taken_slug(
    make_api: MakeApi, env: Env, tenant: Tenant, signup_on: None
) -> None:
    api = await make_api()
    response = await api.post(
        "/v1/signup", {"email": "x@y.test", "slug": tenant.slug, "organisation_name": "Dup"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "slug_taken"


async def test_signup_race_for_one_slug_fails_the_second_cleanly(
    make_api: MakeApi, env: Env, signup_on: None
) -> None:
    slug = f"race-{uuid.uuid4().hex[:8]}"
    ids = []
    for who in ("one", "two"):
        api = await make_api()
        email = f"{who}@{slug}.test"
        started = await api.post(
            "/v1/signup", {"email": email, "slug": slug, "organisation_name": who}
        )
        assert started.status_code == 202
        await api.post(
            "/v1/signup/verify",
            {"token": await env.mailbox.token_for(email, template_word="verify")},
        )
        ids.append(uuid.UUID(started.json()["signup_id"]))

    @ops_task
    async def provision(signup_id: uuid.UUID) -> None:
        async with env.db.ops_session() as s:
            await signup.provision(s, env.settings, env.kek, signup_id)

    for signup_id in ids:
        await provision(signup_id)
    rows = await env.sql(
        "SELECT status FROM platform.signup_requests WHERE id = ANY(:i) ORDER BY created_at",
        i=ids,
    )
    assert [r[0] for r in rows] == ["completed", "failed"]
    ((tenant_id,),) = await env.sql("SELECT id FROM platform.tenants WHERE slug = :s", s=slug)
    env.tenant_ids.append(tenant_id)


async def test_signup_is_rate_limited_per_email(api: Api, signup_on: None) -> None:
    email = f"spam-{uuid.uuid4().hex[:8]}@example.test"
    codes = [
        (
            await api.post(
                "/v1/signup",
                {"email": email, "slug": f"s-{uuid.uuid4().hex[:8]}", "organisation_name": "S"},
            )
        ).status_code
        for _ in range(4)
    ]
    assert codes == [202, 202, 202, 429]
