# SPDX-License-Identifier: AGPL-3.0-only
"""OIDC single sign-on (authorization code + PKCE), per-tenant IdP configuration.

JOSE handling (JWKS, ID-token signature and claims) uses joserfc, the JOSE library
from the Authlib project.

Google, Microsoft Entra and any standards-compliant provider are configured the
same way: an issuer URL, a client id and a client secret (stored encrypted with the
tenant data key). The login flow:

1. ``discover(email)``: which tenant configs claim the email's domain.
2. ``start(...)``: build the authorisation URL; state, nonce and the PKCE verifier
   go in a short-lived HMAC-signed cookie (nothing is stored server-side).
3. ``complete(...)``: check the state, exchange the code, validate the ID token
   (signature against the issuer's JWKS, iss, aud, exp, nonce, verified email in an
   allowed domain), then link or JIT-provision the user and open a session.

A session from SSO counts as MFA-verified: the identity provider owns the second
factor for SSO users (ADR 0010).
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.service import Client, SignedIn, pre_tenant
from pickwise.platform.auth.sessions import create_session
from pickwise.platform.crypto import KeyEncryptionKey, load_tenant_keyring
from pickwise.platform.provisioning.service import add_member, ensure_user
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import set_context
from pickwise.shared.errors import AppError, ForbiddenError, NotFoundError
from pickwise.shared.logging import get_logger
from pickwise.shared.settings import Settings

log = get_logger(__name__)

STATE_COOKIE = "pw_sso"
STATE_TTL_SECONDS = 600
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256")


class SsoError(AppError):
    status_code = 400
    code = "sso_failed"


@dataclass(frozen=True, slots=True)
class SsoConfig:
    id: uuid.UUID
    tenant_id: uuid.UUID
    issuer: str
    client_id: str
    client_secret_enc: bytes
    allowed_domains: tuple[str, ...]
    jit_provisioning: bool
    default_role_key: str | None


@dataclass(frozen=True, slots=True)
class Discovered:
    tenant_id: uuid.UUID
    sso_config_id: uuid.UUID
    enforce_sso: bool


def s256_challenge(verifier: str) -> str:
    """PKCE S256 code challenge (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def client_secret_aad(config_id: uuid.UUID) -> bytes:
    return f"tenant_sso_configs.client_secret_enc:{config_id}".encode()


def redirect_uri(settings: Settings) -> str:
    return f"{settings.public_base_url}/api/v1/auth/sso/callback"


async def discover(db: AsyncSession, email: str) -> list[Discovered]:
    domain = email.rsplit("@", 1)[-1].strip().lower()
    rows = (
        await db.execute(
            text(
                "SELECT tenant_id, sso_config_id, enforce_sso "
                "FROM platform.resolve_sso_by_domain(:d)"
            ),
            {"d": domain},
        )
    ).all()
    return [Discovered(*r) for r in rows]


async def load_config(
    db: AsyncSession, client: Client, tenant_id: uuid.UUID, config_id: uuid.UUID
) -> SsoConfig:
    await set_context(
        db, RequestContext(ActorType.SYSTEM, tenant_id, None, client.request_id, client.ip)
    )
    row = (
        await db.execute(
            text(
                "SELECT c.id, c.tenant_id, c.issuer, c.client_id, c.client_secret_enc, "
                "       c.allowed_domains, c.jit_provisioning, r.key "
                "FROM platform.tenant_sso_configs c "
                "JOIN platform.tenants t ON t.id = c.tenant_id AND t.status = 'active' "
                "LEFT JOIN platform.roles r "
                "  ON r.tenant_id = c.tenant_id AND r.id = c.default_role_id "
                "WHERE c.id = :id"
            ),
            {"id": config_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("That sign-in option isn't available.")
    return SsoConfig(
        row[0],
        row[1],
        row[2].rstrip("/"),
        row[3],
        bytes(row[4]),
        tuple(str(d).lower() for d in row[5]),
        row[6],
        row[7],
    )


async def _metadata(http: httpx.AsyncClient, issuer: str) -> dict[str, Any]:
    response = await http.get(f"{issuer}/.well-known/openid-configuration", timeout=10)
    response.raise_for_status()
    metadata: dict[str, Any] = response.json()
    if metadata.get("issuer", "").rstrip("/") != issuer:
        raise SsoError("The identity provider's metadata doesn't match its issuer.")
    return metadata


# --- the signed state cookie --------------------------------------------------------


def _sign(settings: Settings, payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    mac = hmac.new(
        settings.session_secret.get_secret_value().encode(), body.encode(), hashlib.sha256
    )
    return f"{body}.{base64.urlsafe_b64encode(mac.digest()).decode()}"


def _verify(settings: Settings, value: str) -> dict[str, Any]:
    body, _, signature = value.partition(".")
    expected = hmac.new(
        settings.session_secret.get_secret_value().encode(), body.encode(), hashlib.sha256
    )
    if not secrets.compare_digest(base64.urlsafe_b64encode(expected.digest()).decode(), signature):
        raise SsoError("The sign-in attempt couldn't be verified. Start again.")
    payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body))
    if payload.get("exp", 0) < time.time():
        raise SsoError("The sign-in attempt expired. Start again.")
    return payload


@dataclass(frozen=True, slots=True)
class Started:
    authorization_url: str
    state_cookie: str


async def start(
    settings: Settings, http: httpx.AsyncClient, config: SsoConfig, login_hint: str | None
) -> Started:
    metadata = await _metadata(http, config.issuer)
    state, nonce, verifier = (
        secrets.token_urlsafe(24),
        secrets.token_urlsafe(24),
        secrets.token_urlsafe(48),
    )
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri(settings),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": s256_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    cookie = _sign(
        settings,
        {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "tenant_id": str(config.tenant_id),
            "config_id": str(config.id),
            "exp": int(time.time()) + STATE_TTL_SECONDS,
        },
    )
    return Started(f"{metadata['authorization_endpoint']}?{urlencode(params)}", cookie)


def read_state(settings: Settings, cookie: str | None, state: str) -> dict[str, Any]:
    if not cookie:
        raise SsoError("The sign-in attempt expired. Start again.")
    payload = _verify(settings, cookie)
    if not secrets.compare_digest(payload["state"], state):
        raise SsoError("The sign-in attempt couldn't be verified. Start again.")
    return payload


async def complete(
    db: AsyncSession,
    settings: Settings,
    kek: KeyEncryptionKey,
    http: httpx.AsyncClient,
    client: Client,
    state: dict[str, Any],
    code: str,
) -> SignedIn:
    tenant_id, config_id = uuid.UUID(state["tenant_id"]), uuid.UUID(state["config_id"])
    config = await load_config(db, client, tenant_id, config_id)
    keyring = await load_tenant_keyring(db, kek, tenant_id)
    client_secret = keyring.decrypt(config.client_secret_enc, client_secret_aad(config.id)).decode()

    metadata = await _metadata(http, config.issuer)
    token_response = await http.post(
        metadata["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(settings),
            "code_verifier": state["verifier"],
        },
        auth=(config.client_id, client_secret),
        timeout=10,
    )
    if token_response.status_code != 200:
        log.warning(
            "sso token exchange failed", status=token_response.status_code, config=str(config.id)
        )
        raise SsoError("The identity provider refused the sign-in.")
    id_token = token_response.json().get("id_token")
    if not id_token:
        raise SsoError("The identity provider didn't return an ID token.")
    jwks = (await http.get(metadata["jwks_uri"], timeout=10)).json()
    claims = _validate_id_token(id_token, jwks, config, state["nonce"])

    email = str(claims["email"]).strip().lower()
    subject = str(claims["sub"])
    provider = f"oidc:{config.id}"
    await pre_tenant(db, client)
    user_id = (
        await db.execute(
            text(
                "SELECT user_id FROM platform.user_identities WHERE provider = :p AND subject = :s"
            ),
            {"p": provider, "s": subject},
        )
    ).scalar_one_or_none()
    if user_id is None:
        existing: uuid.UUID | None = (
            await db.execute(text("SELECT id FROM platform.users WHERE email = :e"), {"e": email})
        ).scalar_one_or_none()
        if existing is not None:
            # Tenants choose their own allowed domains, so an IdP may only claim an
            # existing account that already belongs to this tenant (never take over a
            # user of other tenants by email alone).
            await set_context(db, RequestContext(ActorType.SYSTEM, tenant_id, None))
            member = (
                await db.execute(
                    text(
                        "SELECT 1 FROM platform.memberships "
                        "WHERE user_id = :u AND status IN ('invited', 'active')"
                    ),
                    {"u": existing},
                )
            ).first()
            await pre_tenant(db, client)
            if member is None:
                raise ForbiddenError(
                    "An account with this email already exists. Ask an administrator of this "
                    "organisation to invite it.",
                    code="sso_account_exists",
                )
            user_id = existing
        elif config.jit_provisioning:
            user_id = await ensure_user(db, email, str(claims.get("name") or email.split("@")[0]))
        else:
            raise ForbiddenError(
                "You don't have an account yet. Ask your administrator for an invitation.",
                code="sso_no_account",
            )
        await db.execute(
            text(
                "INSERT INTO platform.user_identities (user_id, provider, subject, tenant_id) "
                "VALUES (:u, :p, :s, :t)"
            ),
            {"u": user_id, "p": provider, "s": subject, "t": tenant_id},
        )
    await db.execute(
        text(
            "UPDATE platform.users SET email_verified_at = coalesce(email_verified_at, now()), "
            "last_login_at = now() WHERE id = :u AND status = 'active'"
        ),
        {"u": user_id},
    )

    await set_context(
        db, RequestContext(ActorType.USER, tenant_id, user_id, client.request_id, client.ip)
    )
    membership: str | None = (
        await db.execute(
            text("SELECT status FROM platform.memberships WHERE user_id = :u"), {"u": user_id}
        )
    ).scalar_one_or_none()
    if membership in (None, "removed", "suspended"):
        if membership is None and config.jit_provisioning:
            await add_member(
                db, tenant_id, user_id, config.default_role_key or "employee", status="active"
            )
        else:
            raise ForbiddenError(
                "You don't have access to this organisation.", code="no_membership"
            )
    elif membership == "invited":
        await db.execute(
            text(
                "UPDATE platform.memberships SET status = 'active', joined_at = now() "
                "WHERE user_id = :u"
            ),
            {"u": user_id},
        )

    await pre_tenant(db, client, user_id=user_id)
    token, record = await create_session(
        db,
        settings,
        user_id=user_id,
        tenant_id=tenant_id,
        mfa_verified=True,
        ip=client.ip,
        user_agent=client.user_agent,
        sso_tenant_id=tenant_id,
    )
    await db.execute(
        text("SELECT audit.log_platform_event('login.succeeded', :u, :r, CAST(:ip AS inet))"),
        {"u": user_id, "r": client.request_id, "ip": client.ip},
    )
    return SignedIn(token, record)


def _validate_id_token(
    id_token: str, jwks: dict[str, Any], config: SsoConfig, nonce: str
) -> dict[str, Any]:
    try:
        token = jwt.decode(
            id_token,
            KeySet.import_key_set(jwks),  # type: ignore[arg-type]  # a JWKS document
            algorithms=list(ALLOWED_ALGORITHMS),
        )
        jwt.JWTClaimsRegistry(
            leeway=60,
            iss={"essential": True, "value": config.issuer},
            aud={"essential": True, "value": config.client_id},
            exp={"essential": True},
            sub={"essential": True},
            nonce={"essential": True, "value": nonce},
            email={"essential": True},
        ).validate(token.claims)
    except (JoseError, ValueError) as exc:
        log.warning("sso id token rejected", error=type(exc).__name__, config=str(config.id))
        raise SsoError("The identity provider's response couldn't be verified.") from exc
    claims: dict[str, Any] = token.claims
    if claims.get("email_verified") is not True:
        raise SsoError("Your identity provider hasn't verified your email address.")
    domain = str(claims["email"]).rsplit("@", 1)[-1].lower()
    if domain not in config.allowed_domains:
        raise ForbiddenError(
            "That email domain isn't allowed for this organisation.", code="sso_domain"
        )
    return claims
