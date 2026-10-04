# SPDX-License-Identifier: AGPL-3.0-only
"""OIDC single sign-on against an in-process mock identity provider."""

import base64
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from tests.integration.conftest import MakeApi
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db

ISSUER = "https://idp.example.test"
CLIENT_ID = "pickwise-test-client"
CLIENT_SECRET = "s3cret-from-the-idp"


class MockIdp:
    """A minimal OpenID provider: discovery, JWKS, and a token endpoint."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, {"kid": "good", "use": "sig", "alg": "RS256"})
        self.rogue = RSAKey.generate_key(2048, {"kid": "good", "use": "sig", "alg": "RS256"})
        self.codes: dict[str, dict[str, Any]] = {}
        self.token_requests: list[dict[str, str]] = []

    def issue(
        self,
        *,
        nonce: str,
        challenge: str,
        email: str,
        sub: str | None = None,
        aud: str = CLIENT_ID,
        iss: str = ISSUER,
        email_verified: bool = True,
        expires_in: int = 300,
        signed_by_rogue: bool = False,
        refuse: bool = False,
    ) -> str:
        code = uuid.uuid4().hex
        now = int(time.time())
        claims = {
            "iss": iss,
            "aud": aud,
            "sub": sub or f"sub-{email}",
            "email": email,
            "email_verified": email_verified,
            "name": "Sso Person",
            "nonce": nonce,
            "iat": now,
            "exp": now + expires_in,
        }
        key = self.rogue if signed_by_rogue else self.key
        self.codes[code] = {
            "challenge": challenge,
            "refuse": refuse,
            "id_token": jwt.encode({"alg": "RS256", "kid": "good"}, claims, key),
        }
        return code

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json=KeySet([self.key]).as_dict(private=False))
        if path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            entry = self.codes.pop(form.get("code", ""), None)
            if request.headers.get("authorization") != expected or entry is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            digest = hashlib.sha256(verifier.encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if entry["refuse"] or challenge != entry["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"id_token": entry["id_token"], "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
async def idp(app: FastAPI) -> AsyncIterator[MockIdp]:
    provider = MockIdp()
    original = app.state.http_client
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    yield provider
    await app.state.http_client.aclose()
    app.state.http_client = original


async def configure(
    admin: Api, tenant: Tenant, *, jit: bool = False, enforce: bool = False
) -> tuple[str, str]:
    """Create the tenant's SSO config; returns (config id, email domain)."""
    domain = f"{tenant.slug}.test"
    response = await admin.post(
        "/v1/admin/sso-configs",
        {
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "allowed_domains": [domain],
            "enforce_sso": enforce,
            "jit_provisioning": jit,
            "default_role_key": "employee",
        },
    )
    assert response.status_code == 201, response.text
    assert "client_secret" not in response.text
    return response.json()["id"], domain


async def begin(api: Api, tenant: Tenant, config_id: str) -> dict[str, str]:
    start = await api.get(
        "/v1/auth/sso/start", params={"tenant_id": str(tenant.id), "sso_config_id": config_id}
    )
    assert start.status_code == 303, start.text
    location = urlparse(start.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ISSUER}/authorize"
    return {k: v[0] for k, v in parse_qs(location.query).items()}


async def callback(
    api: Api,
    idp: MockIdp,
    params: dict[str, str],
    email: str,
    *,
    state: str | None = None,
    **claims: Any,
) -> httpx.Response:
    code = idp.issue(
        nonce=claims.pop("nonce", params["nonce"]),
        challenge=params["code_challenge"],
        email=email,
        **claims,
    )
    return await api.get(
        "/v1/auth/sso/callback", params={"code": code, "state": state or params["state"]}
    )


async def admin_for(make_api: MakeApi, tenant: Tenant) -> Api:
    return await (await make_api()).sign_in(tenant.admin)


async def test_discovery_and_authorization_request(
    make_api: MakeApi, api: Api, tenant: Tenant, idp: MockIdp
) -> None:
    config_id, domain = await configure(await admin_for(make_api, tenant), tenant)
    found = await api.get("/v1/auth/sso/discover", params={"email": f"someone@{domain}"})
    assert found.json()["options"] == [
        {"tenant_id": str(tenant.id), "sso_config_id": config_id, "enforced": False}
    ]
    assert (await api.get("/v1/auth/sso/discover", params={"email": "a@nowhere.test"})).json() == {
        "options": []
    }

    params = await begin(api, tenant, config_id)
    assert params["client_id"] == CLIENT_ID
    assert params["response_type"] == "code"
    assert params["code_challenge_method"] == "S256"
    assert params["redirect_uri"].endswith("/api/v1/auth/sso/callback")
    assert {"state", "nonce", "code_challenge"} <= params.keys()
    assert "pw_sso" in api.http.cookies


async def test_happy_path_with_jit_provisioning(
    make_api: MakeApi, api: Api, env: Env, tenant: Tenant, idp: MockIdp
) -> None:
    config_id, domain = await configure(await admin_for(make_api, tenant), tenant, jit=True)
    email = f"new.person@{domain}"
    params = await begin(api, tenant, config_id)
    response = await callback(api, idp, params, email)
    assert response.status_code == 303
    assert response.headers["location"] == f"{api.settings.public_base_url}/"
    assert idp.token_requests[-1]["grant_type"] == "authorization_code"

    me = (await api.get("/v1/me")).json()
    assert me["stage"] == "ready"
    assert me["user"]["email"] == email
    assert me["active_tenant"]["slug"] == tenant.slug
    # SSO counts as multi-factor, and the session is pinned to this tenant.
    ((mfa, pinned),) = await env.sql(
        "SELECT mfa_verified, sso_tenant_id FROM platform.sessions s "
        "JOIN platform.users u ON u.id = s.user_id WHERE u.email = :e AND s.revoked_at IS NULL",
        e=email,
    )
    assert mfa is True
    assert pinned == tenant.id
    # The state cookie is single use.
    assert api.http.cookies.get("pw_sso") is None


async def test_without_jit_an_unknown_person_is_refused(
    make_api: MakeApi, api: Api, tenant: Tenant, idp: MockIdp
) -> None:
    config_id, domain = await configure(await admin_for(make_api, tenant), tenant, jit=False)
    params = await begin(api, tenant, config_id)
    response = await callback(api, idp, params, f"stranger@{domain}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "sso_no_account"
    assert (await api.get("/v1/me")).status_code == 401


async def test_invited_member_signs_in_and_is_activated(
    make_api: MakeApi, api: Api, env: Env, tenant: Tenant, idp: MockIdp
) -> None:
    admin = await admin_for(make_api, tenant)
    config_id, domain = await configure(admin, tenant)
    invited = await env.add_account(tenant, "employee", email=f"invited@{domain}", status="invited")
    params = await begin(api, tenant, config_id)
    assert (await callback(api, idp, params, invited.email)).status_code == 303
    assert (await api.get("/v1/me")).json()["stage"] == "ready"
    members = {m["email"]: m for m in (await admin.get("/v1/admin/users")).json()}
    assert members[invited.email]["status"] == "active"


async def test_an_idp_cannot_take_over_an_account_of_another_tenant(
    make_api: MakeApi, api: Api, env: Env, tenant: Tenant, idp: MockIdp
) -> None:
    config_id, domain = await configure(await admin_for(make_api, tenant), tenant, jit=True)
    other = await env.create_tenant()
    victim = await env.add_account(other, "employee", email=f"victim@{domain}")
    params = await begin(api, tenant, config_id)
    response = await callback(api, idp, params, victim.email)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "sso_account_exists"
    assert (await api.get("/v1/me")).status_code == 401


async def test_email_domain_must_be_allowed(
    make_api: MakeApi, api: Api, tenant: Tenant, idp: MockIdp
) -> None:
    config_id, _ = await configure(await admin_for(make_api, tenant), tenant, jit=True)
    params = await begin(api, tenant, config_id)
    response = await callback(api, idp, params, "attacker@evil.test")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "sso_domain"


@pytest.mark.parametrize(
    "claims",
    [
        {"signed_by_rogue": True},
        {"aud": "someone-elses-client"},
        {"iss": "https://other-idp.example.test"},
        {"expires_in": -3600},
        {"nonce": "not-the-nonce"},
        {"email_verified": False},
        {"refuse": True},
    ],
    ids=[
        "bad-signature",
        "wrong-audience",
        "wrong-issuer",
        "expired",
        "wrong-nonce",
        "unverified-email",
        "token-endpoint-refuses",
    ],
)
async def test_invalid_id_tokens_are_rejected(
    make_api: MakeApi, api: Api, tenant: Tenant, idp: MockIdp, claims: dict[str, Any]
) -> None:
    config_id, domain = await configure(await admin_for(make_api, tenant), tenant, jit=True)
    params = await begin(api, tenant, config_id)
    response = await callback(api, idp, params, f"person@{domain}", **claims)
    assert response.status_code in (400, 403)
    assert response.json()["error"]["code"] == "sso_failed"
    assert (await api.get("/v1/me")).status_code == 401


async def test_state_must_match_the_cookie(
    make_api: MakeApi, api: Api, tenant: Tenant, idp: MockIdp
) -> None:
    config_id, domain = await configure(await admin_for(make_api, tenant), tenant, jit=True)
    params = await begin(api, tenant, config_id)
    response = await callback(api, idp, params, f"person@{domain}", state="forged-state")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "sso_failed"


async def test_callback_without_the_state_cookie_is_refused(
    make_api: MakeApi, api: Api, tenant: Tenant, idp: MockIdp
) -> None:
    config_id, domain = await configure(await admin_for(make_api, tenant), tenant, jit=True)
    params = await begin(api, tenant, config_id)
    api.http.cookies.delete("pw_sso")
    response = await callback(api, idp, params, f"person@{domain}")
    assert response.status_code == 400


async def test_tampered_state_cookie_is_refused(
    make_api: MakeApi, api: Api, tenant: Tenant, idp: MockIdp
) -> None:
    config_id, domain = await configure(await admin_for(make_api, tenant), tenant, jit=True)
    params = await begin(api, tenant, config_id)
    cookie = api.http.cookies.get("pw_sso") or ""
    body, _, mac = cookie.partition(".")
    payload = json.loads(base64.urlsafe_b64decode(body))
    payload["tenant_id"] = str(uuid.uuid4())
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    api.http.cookies.set("pw_sso", f"{forged}.{mac}")
    response = await callback(api, idp, params, f"person@{domain}")
    assert response.status_code == 400


async def test_a_sso_session_cannot_switch_tenant(
    make_api: MakeApi, api: Api, env: Env, tenant: Tenant, idp: MockIdp
) -> None:
    config_id, domain = await configure(await admin_for(make_api, tenant), tenant, jit=True)
    other = await env.create_tenant()
    email = f"two.orgs@{domain}"
    # They already belong to a second organisation, through their own account...
    member = await env.add_account(tenant, "employee", email=email)
    await env.add_account(other, "employee", email=email, user_id=member.user_id)
    params = await begin(api, tenant, config_id)
    assert (await callback(api, idp, params, email)).status_code == 303
    refused = await api.post("/v1/session/tenant", {"tenant_id": str(other.id)})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "sso_session_pinned"
    assert (await api.get("/v1/me")).json()["active_tenant"]["slug"] == tenant.slug


async def test_enforced_sso_blocks_password_login(
    make_api: MakeApi, api: Api, env: Env, tenant: Tenant, idp: MockIdp
) -> None:
    admin = await admin_for(make_api, tenant)
    _, domain = await configure(admin, tenant, enforce=True)
    member = await env.add_account(tenant, "employee", email=f"member@{domain}")
    refused = await api.login(member.email)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "sso_required"
    found = await api.get("/v1/auth/sso/discover", params={"email": member.email})
    assert found.json()["options"][0]["enforced"] is True


async def test_the_idp_must_answer_with_the_expected_issuer(
    make_api: MakeApi, api: Api, tenant: Tenant, idp: MockIdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_id, _ = await configure(await admin_for(make_api, tenant), tenant)
    original = idp.__call__

    def lying(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if request.url.path.endswith("openid-configuration"):
            data = response.json() | {"issuer": "https://elsewhere.example.test"}
            return httpx.Response(200, json=data)
        return response

    api_app: FastAPI = api.http._transport.app  # type: ignore[attr-defined]
    await api_app.state.http_client.aclose()
    api_app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(lying))
    response = await api.get(
        "/v1/auth/sso/start", params={"tenant_id": str(tenant.id), "sso_config_id": config_id}
    )
    assert response.status_code == 400
