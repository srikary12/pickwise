# SPDX-License-Identifier: AGPL-3.0-only
"""Login, logout, CSRF, lockout, rate limits, sessions, passwords and invites over HTTP."""

import uuid

import pytest

from pickwise.platform import ratelimit
from tests.integration.conftest import MakeApi
from tests.integration.support import PASSWORD, Api, Env, Tenant

pytestmark = pytest.mark.db


async def test_login_me_logout(api: Api, tenant: Tenant) -> None:
    response = await api.login(tenant.admin.email)
    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "ready"
    assert body["active_tenant"]["slug"] == tenant.slug
    assert "platform.users.read" in body["permissions"]
    assert response.headers["cache-control"] == "no-store"

    me = await api.get("/v1/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == tenant.admin.email

    assert (await api.post("/v1/auth/logout")).status_code == 204
    assert api.session_cookie is None
    assert (await api.get("/v1/me")).status_code == 401


async def test_logged_out_token_stays_dead(api: Api, tenant: Tenant) -> None:
    await api.sign_in(tenant.admin)
    token = api.session_cookie
    await api.post("/v1/auth/logout")
    api.http.cookies.set("pw_session", token or "")
    assert (await api.get("/v1/me")).status_code == 401


async def test_wrong_password_and_unknown_email_look_identical(api: Api, tenant: Tenant) -> None:
    wrong = await api.login(tenant.admin.email, "not the password at all")
    unknown = await api.login(f"nobody-{uuid.uuid4().hex}@example.test", "whatever password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"


async def test_security_headers(api: Api) -> None:
    response = await api.get("/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]


async def test_session_cookie_is_httponly_and_lax(api: Api, tenant: Tenant) -> None:
    response = await api.login(tenant.admin.email)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie


# --- CSRF -------------------------------------------------------------------------------


async def test_csrf_required_for_cookie_requests(
    make_api: MakeApi, api: Api, tenant: Tenant
) -> None:
    await api.sign_in(tenant.admin)
    bare = await make_api()
    bare.http.headers.pop("X-CSRF-Token")
    bare.http.cookies.set("pw_session", api.session_cookie or "")
    refused = await bare.post("/v1/auth/logout")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "csrf"


async def test_csrf_header_must_match_cookie(api: Api, tenant: Tenant) -> None:
    await api.sign_in(tenant.admin)
    response = await api.post("/v1/auth/logout", headers={"X-CSRF-Token": "forged"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf"


async def test_csrf_refuses_cross_site_requests(api: Api, tenant: Tenant) -> None:
    await api.sign_in(tenant.admin)
    cross = await api.post("/v1/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403
    other_origin = await api.post("/v1/auth/logout", headers={"Origin": "https://evil.example"})
    assert other_origin.status_code == 403
    same = await api.post(
        "/v1/auth/logout",
        headers={"Sec-Fetch-Site": "same-origin", "Origin": api.settings.public_base_url},
    )
    assert same.status_code == 204


async def test_get_requests_need_no_csrf(make_api: MakeApi, api: Api, tenant: Tenant) -> None:
    await api.sign_in(tenant.admin)
    bare = await make_api()
    bare.http.headers.pop("X-CSRF-Token")
    bare.http.cookies.set("pw_session", api.session_cookie or "")
    assert (await bare.get("/v1/me")).status_code == 200


# --- lockout and rate limits ---------------------------------------------------------------


async def test_account_locks_after_five_failures(
    api: Api, tenant: Tenant, monkeypatch: pytest.MonkeyPatch, env: Env
) -> None:
    # The per-email rate limit would trip first; widen it to isolate the lockout.
    monkeypatch.setattr(ratelimit, "LOGIN_PER_EMAIL", ratelimit.Rule("login:email:t", 100, 60))
    account = await env.add_account(tenant, "employee")
    for _ in range(5):
        assert (await api.login(account.email, "wrong password here")).status_code == 401
    # Even the right password is refused while locked, with the same generic message.
    locked = await api.login(account.email)
    assert locked.status_code == 401
    assert locked.json()["error"]["code"] == "invalid_credentials"
    # ...until the lock expires.
    await env.sql(
        "UPDATE platform.users SET locked_until = now() - interval '1 second' WHERE id = :u",
        u=account.user_id,
    )
    assert (await api.login(account.email)).status_code == 200


async def test_failed_logins_are_counted_and_audited(api: Api, tenant: Tenant, env: Env) -> None:
    await api.login(tenant.admin.email, "wrong password here")
    ((count,),) = await env.sql(
        "SELECT failed_login_count FROM platform.users WHERE id = :u", u=tenant.admin.user_id
    )
    assert count == 1
    events = await env.sql(
        "SELECT count(*) FROM audit.events WHERE action = 'login.failed' AND actor_user_id = :u",
        u=tenant.admin.user_id,
    )
    assert events[0][0] >= 1


async def test_login_is_rate_limited_per_email(api: Api, tenant: Tenant) -> None:
    statuses = [
        (await api.login(tenant.admin.email, "wrong password here")).status_code for _ in range(6)
    ]
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
    limited = await api.login(tenant.admin.email, "wrong password here")
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


async def test_login_is_rate_limited_per_ip(api: Api, tenant: Tenant) -> None:
    codes = [
        (await api.login(f"x{i}-{uuid.uuid4().hex[:6]}@example.test", "wrong password")).status_code
        for i in range(22)
    ]
    assert 429 in codes
    # Tokens refill continuously, so a slow run may admit one extra attempt.
    assert codes.index(429) in (20, 21)


# --- sessions ---------------------------------------------------------------------------------


async def test_idle_session_expires(api: Api, tenant: Tenant, env: Env) -> None:
    await api.sign_in(tenant.admin)
    await env.sql(
        "UPDATE platform.sessions SET last_seen_at = now() - interval '2 hours' WHERE user_id = :u",
        u=tenant.admin.user_id,
    )
    assert (await api.get("/v1/me")).status_code == 401


async def test_absolute_session_lifetime(api: Api, tenant: Tenant, env: Env) -> None:
    await api.sign_in(tenant.admin)
    await env.sql(
        "UPDATE platform.sessions SET expires_at = now() - interval '1 second' WHERE user_id = :u",
        u=tenant.admin.user_id,
    )
    assert (await api.get("/v1/me")).status_code == 401


async def test_only_the_hash_of_the_token_is_stored(api: Api, tenant: Tenant, env: Env) -> None:
    await api.sign_in(tenant.admin)
    token = api.session_cookie or ""
    rows = await env.sql(
        "SELECT count(*) FROM platform.sessions WHERE user_id = :u AND token_hash = :h",
        u=tenant.admin.user_id,
        h=token.encode(),
    )
    assert rows[0][0] == 0


async def test_suspended_user_loses_access(api: Api, tenant: Tenant, env: Env) -> None:
    account = await env.add_account(tenant, "employee")
    await api.sign_in(account)
    await env.sql(
        "UPDATE platform.memberships SET status = 'suspended' WHERE id = :m",
        m=account.membership_id,
    )
    me = await api.get("/v1/me")
    # Still a person, but no longer in any organisation.
    assert me.status_code == 200
    assert me.json()["stage"] == "tenant_selection"
    assert (await api.get("/v1/admin/users")).status_code == 403


async def test_tenant_switch_rotates_the_session(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    other = await env.create_tenant()
    traveller = await env.add_account(tenant, "hr_admin")
    await env.add_account(other, "hr_admin", email=traveller.email, user_id=traveller.user_id)
    api = await make_api()
    login = await api.login(traveller.email)
    assert login.json()["stage"] == "tenant_selection"
    assert len(login.json()["tenants"]) == 2
    before = api.session_cookie
    # Nothing tenant-bound works until a tenant is chosen.
    assert (await api.get("/v1/admin/users")).status_code == 403

    switched = await api.post("/v1/session/tenant", {"tenant_id": str(tenant.id)})
    assert switched.status_code == 200
    assert switched.json()["stage"] == "ready"
    after = api.session_cookie
    assert after
    assert after != before

    stale = await make_api()
    stale.http.cookies.set("pw_session", before or "")
    assert (await stale.get("/v1/me")).status_code == 401  # the old token was revoked

    back = await api.post("/v1/session/tenant", {"tenant_id": str(other.id)})
    assert back.json()["active_tenant"]["slug"] == other.slug
    assert api.session_cookie not in (before, after)


async def test_cannot_switch_to_a_tenant_without_membership(
    api: Api, env: Env, tenant: Tenant
) -> None:
    stranger = await env.create_tenant()
    traveller = await env.add_account(tenant, "hr_admin")
    await api.sign_in(traveller)
    refused = await api.post("/v1/session/tenant", {"tenant_id": str(stranger.id)})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "no_membership"


async def test_tenant_data_is_isolated_between_tenants(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    other = await env.create_tenant()
    mine = await (await make_api()).sign_in(tenant.admin)
    theirs = await (await make_api()).sign_in(other.admin)
    mine_emails = {u["email"] for u in (await mine.get("/v1/admin/users")).json()}
    their_emails = {u["email"] for u in (await theirs.get("/v1/admin/users")).json()}
    assert tenant.admin.email in mine_emails
    assert other.admin.email in their_emails
    assert mine_emails.isdisjoint(their_emails)


# --- passwords --------------------------------------------------------------------------------


async def test_forgot_password_does_not_reveal_accounts(api: Api, tenant: Tenant, env: Env) -> None:
    known = await api.post("/v1/auth/password/forgot", {"email": tenant.admin.email})
    unknown = await api.post(
        "/v1/auth/password/forgot", {"email": f"nobody-{uuid.uuid4().hex[:8]}@example.test"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    await env.mailbox.deliver()
    assert env.mailbox.count_to(tenant.admin.email) >= 1


async def test_password_reset_flow(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    account = await env.add_account(tenant, "employee")
    signed_in = await (await make_api()).sign_in(account)
    api = await make_api()
    await api.post("/v1/auth/password/forgot", {"email": account.email})
    token = await env.mailbox.token_for(account.email, template_word="reset")

    new_password = "an entirely new passphrase 42"
    assert (
        await api.post("/v1/auth/password/reset", {"token": token, "password": new_password})
    ).status_code == 204
    # Single use.
    again = await api.post("/v1/auth/password/reset", {"token": token, "password": new_password})
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_token"
    # Every existing session was revoked, the old password is dead, the new one works.
    assert (await signed_in.get("/v1/me")).status_code == 401
    assert (await api.login(account.email)).status_code == 401
    assert (await api.login(account.email, new_password)).status_code == 200


async def test_reset_rejects_weak_passwords(api: Api, env: Env, tenant: Tenant) -> None:
    account = await env.add_account(tenant, "employee")
    await api.post("/v1/auth/password/forgot", {"email": account.email})
    token = await env.mailbox.token_for(account.email, template_word="reset")
    weak = await api.post("/v1/auth/password/reset", {"token": token, "password": "short"})
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "weak_password"
    # A rejected attempt doesn't burn the token.
    ok = await api.post(
        "/v1/auth/password/reset", {"token": token, "password": "a long enough passphrase"}
    )
    assert ok.status_code == 204


async def test_expired_reset_token_is_refused(api: Api, env: Env, tenant: Tenant) -> None:
    account = await env.add_account(tenant, "employee")
    await api.post("/v1/auth/password/forgot", {"email": account.email})
    token = await env.mailbox.token_for(account.email, template_word="reset")
    await env.sql(
        "UPDATE platform.auth_tokens SET expires_at = now() - interval '1 minute' "
        "WHERE user_id = :u AND purpose = 'reset_password'",
        u=account.user_id,
    )
    response = await api.post(
        "/v1/auth/password/reset", {"token": token, "password": "a long enough passphrase"}
    )
    assert response.status_code == 400


async def test_forgot_password_is_rate_limited(api: Api, env: Env, tenant: Tenant) -> None:
    account = await env.add_account(tenant, "employee")
    codes = [
        (await api.post("/v1/auth/password/forgot", {"email": account.email})).status_code
        for _ in range(4)
    ]
    assert codes == [202, 202, 202, 429]


async def test_change_password_rotates_and_revokes_others(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    account = await env.add_account(tenant, "employee")
    first = await (await make_api()).sign_in(account)
    second = await (await make_api()).sign_in(account)
    old_token = second.session_cookie

    wrong = await second.post(
        "/v1/auth/password/change",
        {"current_password": "not my password", "new_password": "a brand new passphrase"},
    )
    assert wrong.status_code == 403
    changed = await second.post(
        "/v1/auth/password/change",
        {"current_password": PASSWORD, "new_password": "a brand new passphrase"},
    )
    assert changed.status_code == 204
    assert second.session_cookie != old_token
    assert (await second.get("/v1/me")).status_code == 200
    assert (await first.get("/v1/me")).status_code == 401


async def test_email_verification(api: Api, env: Env, tenant: Tenant) -> None:
    from pickwise.platform.auth.tokens import new_token, token_hash

    token = new_token()
    await env.sql(
        "INSERT INTO platform.auth_tokens (purpose, token_hash, user_id, email, expires_at) "
        "VALUES ('verify_email', :h, :u, :e, now() + interval '1 day')",
        h=token_hash(token),
        u=tenant.admin.user_id,
        e=tenant.admin.email,
    )
    await env.sql(
        "UPDATE platform.users SET email_verified_at = NULL WHERE id = :u", u=tenant.admin.user_id
    )
    assert (await api.post("/v1/auth/email/verify", {"token": token})).status_code == 204
    ((verified,),) = await env.sql(
        "SELECT email_verified_at IS NOT NULL FROM platform.users WHERE id = :u",
        u=tenant.admin.user_id,
    )
    assert verified
    assert (await api.post("/v1/auth/email/verify", {"token": token})).status_code == 400


# --- invites ---------------------------------------------------------------------------------


async def test_invite_accept_flow(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    email = f"new-{uuid.uuid4().hex[:8]}@{tenant.slug}.test"
    invited = await admin.post(
        "/v1/admin/users/invite",
        {"email": email, "display_name": "New Person", "role_key": "employee"},
    )
    assert invited.status_code == 201
    assert invited.json()["status"] == "invited"
    token = await env.mailbox.token_for(email)

    api = await make_api()
    inspected = await api.get(f"/v1/auth/invites/{token}")
    assert inspected.json() == {"valid": True, "needs_password": True}

    no_password = await api.post("/v1/auth/invites/accept", {"token": token})
    assert no_password.status_code == 422
    assert no_password.json()["error"]["code"] == "password_required"

    accepted = await api.post(
        "/v1/auth/invites/accept", {"token": token, "password": "my chosen passphrase"}
    )
    assert accepted.status_code == 200
    assert accepted.json()["stage"] == "ready"
    assert accepted.json()["active_tenant"]["slug"] == tenant.slug
    assert (await api.get("/v1/me")).json()["user"]["email"] == email

    # The link is single use, and the member is now active.
    assert (await api.get(f"/v1/auth/invites/{token}")).json()["valid"] is False
    again = await (await make_api()).post(
        "/v1/auth/invites/accept", {"token": token, "password": "my chosen passphrase"}
    )
    assert again.status_code == 400
    members = {m["email"]: m for m in (await admin.get("/v1/admin/users")).json()}
    assert members[email]["status"] == "active"


async def test_expired_invite_is_invalid(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    email = f"late-{uuid.uuid4().hex[:8]}@{tenant.slug}.test"
    await admin.post(
        "/v1/admin/users/invite", {"email": email, "display_name": "Late", "role_key": "employee"}
    )
    token = await env.mailbox.token_for(email)
    await env.sql(
        "UPDATE platform.auth_tokens SET expires_at = now() - interval '1 minute' "
        "WHERE purpose = 'invite' AND email = :e",
        e=email,
    )
    api = await make_api()
    assert (await api.get(f"/v1/auth/invites/{token}")).json()["valid"] is False


async def test_invite_for_an_existing_user_needs_no_password(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    other = await env.create_tenant()
    admin = await (await make_api()).sign_in(other.admin)
    # An existing account (with a password) gets invited into a second organisation.
    existing = await env.add_account(tenant, "employee")
    await admin.post(
        "/v1/admin/users/invite",
        {"email": existing.email, "display_name": "Existing", "role_key": "employee"},
    )
    token = await env.mailbox.token_for(existing.email, template_word=other.slug)
    api = await make_api()
    assert (await api.get(f"/v1/auth/invites/{token}")).json()["needs_password"] is False
    accepted = await api.post("/v1/auth/invites/accept", {"token": token})
    assert accepted.status_code == 200
    # Joining a second tenant leaves the user with a choice.
    assert accepted.json()["stage"] in ("ready", "tenant_selection")


async def test_invite_lookup_is_rate_limited(api: Api) -> None:
    codes = [
        (await api.get(f"/v1/auth/invites/{uuid.uuid4().hex}{uuid.uuid4().hex}")).status_code
        for _ in range(62)
    ]
    assert codes[-1] == 429
