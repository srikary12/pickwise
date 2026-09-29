# SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI dependencies: the request's database transaction, the caller, CSRF,
and ``require(permission)`` (CLAUDE.md rules 3 and 16).

Every request runs in one transaction that starts with no tenant. Authentication
reads the global session/user tables (or resolves an API key through its SECURITY
DEFINER lookup), then narrows the same transaction to the caller's tenant and user,
so RLS applies to everything the endpoint does.
"""

import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.api_keys import authenticate_api_key
from pickwise.platform.auth.service import (
    ActiveMembership,
    AuthStage,
    Client,
    UserRecord,
    get_user,
    membership_in,
    stage_for,
)
from pickwise.platform.auth.sessions import (
    SessionRecord,
    csrf_cookie_name,
    load_session,
    session_cookie_name,
)
from pickwise.platform.crypto import KeyEncryptionKey
from pickwise.platform.rbac.grants import Grants, load_grants
from pickwise.platform.rbac.principal import Principal
from pickwise.platform.scopes import DataScope
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database, set_context
from pickwise.shared.errors import ForbiddenError, NotAuthenticatedError
from pickwise.shared.settings import Settings

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "X-CSRF-Token"
# Attribute set on dependency callables so a test can prove every route declares
# a permission or is explicitly public.
PERMISSION_MARKER = "__pickwise_permission__"
PUBLIC_MARKER = "__pickwise_public__"
SIGNED_IN_MARKER = "__pickwise_signed_in__"


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_kek(request: Request) -> KeyEncryptionKey:
    kek: KeyEncryptionKey = request.app.state.kek
    return kek


def get_client(request: Request) -> Client:
    return Client(
        ip=getattr(request.state, "client_ip", None),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


async def request_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request, committed before the response is sent."""
    database: Database = request.app.state.database
    client = get_client(request)
    ctx = RequestContext(ActorType.USER, None, None, client.request_id, client.ip)
    async with database.tenant_session(ctx) as session:
        yield session


DB = Annotated[AsyncSession, Depends(request_db, scope="function")]

# A separate, immediately-committed transaction for bookkeeping that must survive
# the request failing: rate-limit spends, failed-login counters, login.failed events.
SideTransaction = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def get_side_transaction(request: Request) -> SideTransaction:
    database: Database = request.app.state.database
    client = get_client(request)

    def open_side() -> AbstractAsyncContextManager[AsyncSession]:
        return database.tenant_session(
            RequestContext(ActorType.USER, None, None, client.request_id, client.ip)
        )

    return open_side


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
KekDep = Annotated[KeyEncryptionKey, Depends(get_kek)]
ClientDep = Annotated[Client, Depends(get_client)]
SideDep = Annotated[SideTransaction, Depends(get_side_transaction)]


def csrf_protect(request: Request, settings: SettingsDep) -> None:
    """Double-submit CSRF check for cookie-authenticated unsafe requests (ADR 0010).

    Bearer (API-key) requests carry no ambient credentials and are exempt.
    """
    if request.method not in UNSAFE_METHODS:
        return
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise ForbiddenError("Cross-site request refused.", code="csrf")
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != settings.public_base_url.rstrip("/"):
        raise ForbiddenError("Cross-origin request refused.", code="csrf")
    cookie = request.cookies.get(csrf_cookie_name(settings), "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise ForbiddenError("Missing or invalid CSRF token.", code="csrf")


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Who is calling, and how far their session has got."""

    principal: Principal | None
    session: SessionRecord | None = None
    user: UserRecord | None = None
    membership: ActiveMembership | None = None
    stage: AuthStage | None = None


async def get_auth(
    request: Request, db: DB, settings: SettingsDep, client: ClientDep
) -> AuthContext:
    cached: AuthContext | None = getattr(request.state, "auth", None)
    if cached is not None:
        return cached
    auth = await _authenticate(request, db, settings, client)
    request.state.auth = auth
    return auth


async def _authenticate(
    request: Request, db: AsyncSession, settings: Settings, client: Client
) -> AuthContext:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        principal = await authenticate_api_key(db, authorization[7:].strip(), client)
        if principal is None:
            raise NotAuthenticatedError("Invalid API key.", code="invalid_api_key")
        return AuthContext(principal, stage=AuthStage.READY)

    token = request.cookies.get(session_cookie_name(settings))
    if not token:
        return AuthContext(None)
    return await authenticate_session(db, settings, client, token)


async def authenticate_session(
    db: AsyncSession, settings: Settings, client: Client, token: str
) -> AuthContext:
    """Resolve a session token to the caller, narrowing the transaction to their tenant."""
    session = await load_session(db, settings, token)
    if session is None:
        return AuthContext(None)
    user = await get_user(db, session.user_id)
    if user is None or user.status != "active":
        return AuthContext(None)
    membership = None
    if session.active_tenant_id is not None and not (user.mfa_enabled and not session.mfa_verified):
        membership = await membership_in(db, user.id, session.active_tenant_id, client)
    principal = Principal(
        ActorType.USER,
        membership.tenant_id if membership else None,
        user_id=user.id,
        membership_id=membership.membership_id if membership else None,
        session_id=session.id,
    )
    await set_context(
        db,
        RequestContext(ActorType.USER, principal.tenant_id, user.id, client.request_id, client.ip),
    )
    return AuthContext(principal, session, user, membership, stage_for(user, session, membership))


AuthDep = Annotated[AuthContext, Depends(get_auth)]


def signed_in(*allowed: AuthStage) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    """A session (any user, not API keys) at one of the allowed stages."""

    async def dependency(auth: AuthDep) -> AuthContext:
        if auth.principal is None or auth.session is None:
            raise NotAuthenticatedError("Sign in to continue.")
        if auth.stage not in allowed:
            raise ForbiddenError(_stage_message(auth.stage), code=str(auth.stage))
        return auth

    setattr(dependency, SIGNED_IN_MARKER, tuple(allowed))
    return dependency


def _stage_message(stage: AuthStage | None) -> str:
    return (
        {
            AuthStage.MFA_PENDING: "Enter your two-factor code to continue.",
            AuthStage.TENANT_SELECTION: "Choose an organisation to continue.",
            AuthStage.MFA_ENROLMENT_REQUIRED: (
                "Your organisation requires two-factor authentication. Set it up to continue."
            ),
        }.get(stage, "Not allowed at this point.")
        if stage
        else "Not allowed at this point."
    )


@dataclass(frozen=True, slots=True)
class Authorized:
    """A caller allowed to use ``permission``, in the listed data scopes."""

    principal: Principal
    permission: str
    scopes: tuple[DataScope, ...]
    grants: Grants

    @property
    def tenant_id(self) -> uuid.UUID:
        assert self.principal.tenant_id is not None  # require() only passes tenant-bound callers
        return self.principal.tenant_id


def require(permission: str) -> Callable[..., Coroutine[Any, Any, Authorized]]:
    """Declare the permission a route needs. Loads the caller's grants once per request."""

    async def dependency(request: Request, auth: AuthDep, db: DB) -> Authorized:
        principal = auth.principal
        if principal is None:
            raise NotAuthenticatedError("Sign in to continue.")
        if auth.stage is not AuthStage.READY or principal.tenant_id is None:
            raise ForbiddenError(_stage_message(auth.stage), code=str(auth.stage or "forbidden"))
        grants: Grants | None = getattr(request.state, "grants", None)
        if grants is None:
            grants = await load_grants(db, principal)
            request.state.grants = grants
        scopes = grants.get(permission)
        if not scopes:
            raise ForbiddenError("You don't have permission to do that.", code="permission_denied")
        return Authorized(principal, permission, scopes, grants)

    setattr(dependency, PERMISSION_MARKER, permission)
    return dependency


def public() -> None:
    """Marks a route that needs no permission (login, health, signup, …)."""


setattr(public, PUBLIC_MARKER, True)


async def principal_tenant_name(db: AsyncSession, tenant_id: uuid.UUID) -> str | None:
    name: str | None = (
        await db.execute(text("SELECT name FROM platform.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none()
    return name
