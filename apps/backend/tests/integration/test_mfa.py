# SPDX-License-Identifier: AGPL-3.0-only
"""TOTP enrolment, verification, replay protection, recovery codes, tenant-required MFA."""

import pytest

from tests.integration.conftest import MakeApi
from tests.integration.support import Account, Api, Env, Tenant, totp

pytestmark = pytest.mark.db


async def enrol(api: Api) -> tuple[str, list[str]]:
    """Set up MFA for the signed-in user; returns (secret, recovery codes)."""
    setup = await api.post("/v1/auth/mfa/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    confirmed = await api.post("/v1/auth/mfa/confirm", {"code": totp(secret)})
    assert confirmed.status_code == 200, confirmed.text
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == 10
    return secret, codes


async def rewind_step(env: Env, account: Account) -> None:
    await env.sql(
        "UPDATE platform.users SET mfa_last_used_step = mfa_last_used_step - 5 WHERE id = :u",
        u=account.user_id,
    )


async def enrolled_account(
    make_api: MakeApi, env: Env, tenant: Tenant, role: str = "employee"
) -> tuple[Account, str, list[str]]:
    account = await env.add_account(tenant, role)
    api = await (await make_api()).sign_in(account)
    secret, codes = await enrol(api)
    return account, secret, codes


async def test_enrolment_rotates_session_and_marks_mfa_enabled(
    api: Api, env: Env, tenant: Tenant
) -> None:
    account = await env.add_account(tenant, "employee")
    await api.sign_in(account)
    before = api.session_cookie
    await enrol(api)
    assert api.session_cookie != before
    me = (await api.get("/v1/me")).json()
    assert me["stage"] == "ready"
    assert me["user"]["mfa_enabled"] is True


async def test_confirm_needs_a_valid_code(api: Api, env: Env, tenant: Tenant) -> None:
    await api.sign_in(await env.add_account(tenant, "employee"))
    await api.post("/v1/auth/mfa/setup")
    bad = await api.post("/v1/auth/mfa/confirm", {"code": "000000"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_code"
    assert (await api.get("/v1/me")).json()["user"]["mfa_enabled"] is False


async def test_seed_is_stored_encrypted(api: Api, env: Env, tenant: Tenant) -> None:
    account = await env.add_account(tenant, "employee")
    await api.sign_in(account)
    secret, _ = await enrol(api)
    ((stored,),) = await env.sql(
        "SELECT mfa_totp_secret_enc FROM platform.users WHERE id = :u", u=account.user_id
    )
    assert secret.encode() not in bytes(stored)


async def test_login_with_mfa_is_two_steps(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    account, secret, _ = await enrolled_account(make_api, env, tenant, "hr_admin")
    api = await make_api()
    login = await api.login(account.email)
    assert login.json()["stage"] == "mfa_pending"
    # Nothing but the second factor works until it's given.
    pending = await api.get("/v1/admin/users")
    assert pending.status_code == 403
    assert pending.json()["error"]["code"] == "mfa_pending"
    assert (await api.post("/v1/session/tenant", {"tenant_id": str(tenant.id)})).status_code == 403

    before = api.session_cookie
    verified = await api.post("/v1/auth/mfa/verify", {"code": totp(secret, 1)})
    assert verified.status_code == 200, verified.text
    assert verified.json()["stage"] == "ready"
    assert api.session_cookie != before
    assert (await api.get("/v1/admin/users")).status_code == 200


async def test_wrong_code_is_refused(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    account, _, _ = await enrolled_account(make_api, env, tenant)
    api = await make_api()
    await api.login(account.email)
    response = await api.post("/v1/auth/mfa/verify", {"code": "123456"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_code"
    # The session is still pending, not upgraded.
    assert (await api.get("/v1/me")).json()["stage"] == "mfa_pending"


async def test_a_totp_code_cannot_be_replayed(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    account, secret, _ = await enrolled_account(make_api, env, tenant)
    code = totp(secret, 1)
    first = await make_api()
    await first.login(account.email)
    assert (await first.post("/v1/auth/mfa/verify", {"code": code})).status_code == 200
    second = await make_api()
    await second.login(account.email)
    replay = await second.post("/v1/auth/mfa/verify", {"code": code})
    assert replay.status_code == 401
    # An older step is refused too; a newer one still works.
    assert (await second.post("/v1/auth/mfa/verify", {"code": totp(secret)})).status_code == 401


async def test_the_confirming_code_cannot_be_reused_to_log_in(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    account = await env.add_account(tenant, "employee")
    api = await (await make_api()).sign_in(account)
    setup = await api.post("/v1/auth/mfa/setup")
    secret = setup.json()["secret"]
    code = totp(secret)
    assert (await api.post("/v1/auth/mfa/confirm", {"code": code})).status_code == 200
    other = await make_api()
    await other.login(account.email)
    assert (await other.post("/v1/auth/mfa/verify", {"code": code})).status_code == 401


async def test_recovery_codes_work_once(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    account, _, codes = await enrolled_account(make_api, env, tenant)
    first = await make_api()
    await first.login(account.email)
    used = await first.post("/v1/auth/mfa/verify", {"recovery_code": codes[0]})
    assert used.status_code == 200
    assert used.json()["stage"] == "ready"

    second = await make_api()
    await second.login(account.email)
    again = await second.post("/v1/auth/mfa/verify", {"recovery_code": codes[0]})
    assert again.status_code == 401
    other = await second.post("/v1/auth/mfa/verify", {"recovery_code": codes[1].upper()})
    assert other.status_code == 200  # case-insensitive


async def test_recovery_codes_are_hashed_at_rest(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    account, _, codes = await enrolled_account(make_api, env, tenant)
    rows = await env.sql(
        "SELECT code_hash FROM platform.mfa_recovery_codes WHERE user_id = :u", u=account.user_id
    )
    assert len(rows) == 10
    plain = codes[0].replace("-", "").encode()
    assert all(plain not in bytes(r[0]) for r in rows)


async def test_regenerating_recovery_codes_invalidates_the_old_ones(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    account, secret, old = await enrolled_account(make_api, env, tenant)
    api = await make_api()
    await api.login(account.email)
    await api.post("/v1/auth/mfa/verify", {"code": totp(secret, 1)})
    refused = await api.post("/v1/auth/mfa/recovery-codes", {"code": "000000"})
    assert refused.status_code == 403
    # The code used to sign in can't be reused; a later step (here, simulated by
    # rewinding the stored step) is accepted.
    assert (
        await api.post("/v1/auth/mfa/recovery-codes", {"code": totp(secret, 1)})
    ).status_code == 403
    await rewind_step(env, account)
    fresh = await api.post("/v1/auth/mfa/recovery-codes", {"code": totp(secret, 1)})
    assert fresh.status_code == 200, fresh.text
    new = fresh.json()["recovery_codes"]
    assert set(new).isdisjoint(old)
    stale = await make_api()
    await stale.login(account.email)
    assert (await stale.post("/v1/auth/mfa/verify", {"recovery_code": old[0]})).status_code == 401


async def test_disable_mfa(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    account, secret, _ = await enrolled_account(make_api, env, tenant)
    api = await make_api()
    await api.login(account.email)
    await api.post("/v1/auth/mfa/verify", {"code": totp(secret, 1)})
    assert (await api.post("/v1/auth/mfa/disable", {"code": "000000"})).status_code == 403
    await rewind_step(env, account)
    assert (await api.post("/v1/auth/mfa/disable", {"code": totp(secret, 1)})).status_code == 204
    assert (await api.get("/v1/me")).json()["user"]["mfa_enabled"] is False


async def test_tenant_requiring_mfa_forces_enrolment(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    strict = await env.create_tenant(mfa_required=True)
    account = await env.add_account(strict, "hr_admin")
    api = await make_api()
    login = await api.login(account.email)
    assert login.json()["stage"] == "mfa_enrolment_required"
    blocked = await api.get("/v1/admin/users")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "mfa_enrolment_required"

    secret, _ = await enrol(api)
    assert (await api.get("/v1/me")).json()["stage"] == "ready"
    assert (await api.get("/v1/admin/users")).status_code == 200

    # The requirement can't be dodged afterwards.
    disabled = await api.post("/v1/auth/mfa/disable", {"code": totp(secret, 1)})
    assert disabled.status_code == 403
    assert disabled.json()["error"]["code"] == "mfa_required_by_tenant"


async def test_admin_can_require_mfa_for_a_tenant(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    current = (await admin.get("/v1/admin/tenant")).json()
    assert current["mfa_required"] is False
    updated = await admin.patch(
        "/v1/admin/tenant", {"mfa_required": True, "row_version": current["row_version"]}
    )
    assert updated.status_code == 200
    assert updated.json()["mfa_required"] is True
    # The admin has no MFA yet, so their very next request sends them to enrol.
    assert (await admin.get("/v1/me")).json()["stage"] == "mfa_enrolment_required"
    # A stale row_version is a conflict.
    stale = await admin.patch(
        "/v1/admin/tenant", {"mfa_required": False, "row_version": current["row_version"]}
    )
    assert stale.status_code in (403, 409)


async def test_mfa_verification_is_rate_limited(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    account, _, _ = await enrolled_account(make_api, env, tenant)
    api = await make_api()
    await api.login(account.email)
    codes = [
        (await api.post("/v1/auth/mfa/verify", {"code": "123456"})).status_code for _ in range(7)
    ]
    assert codes[:5] == [401] * 5
    assert 429 in codes[5:]
