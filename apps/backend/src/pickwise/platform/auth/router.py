# SPDX-License-Identifier: AGPL-3.0-only
"""Authentication and current-user endpoints (/v1/auth, /v1/me, /v1/session)."""

import secrets
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from pickwise.platform import ratelimit
from pickwise.platform.auth import service, sso
from pickwise.platform.auth.dependencies import (
    DB,
    AuthContext,
    AuthDep,
    ClientDep,
    KekDep,
    SettingsDep,
    SideDep,
    SideTransaction,
    authenticate_session,
    public,
    signed_in,
)
from pickwise.platform.auth.schemas import (
    AcceptInviteRequest,
    ChangePasswordRequest,
    CsrfToken,
    ForgotPasswordRequest,
    InviteStatusResponse,
    LoginRequest,
    MfaCodeRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    RecoveryCodes,
    ResetPasswordRequest,
    SessionState,
    SsoDiscovery,
    SsoOption,
    SwitchTenantRequest,
    TenantSummary,
    TokenRequest,
    UserSummary,
)
from pickwise.platform.auth.service import AuthStage, Client, SignedIn
from pickwise.platform.auth.sessions import csrf_cookie_name, revoke, session_cookie_name
from pickwise.platform.branding.service import logo_file_id
from pickwise.platform.notifications.email import dispatch
from pickwise.platform.rbac.grants import load_grants
from pickwise.shared.errors import UnprocessableError
from pickwise.shared.settings import Settings

router = APIRouter(prefix="/v1", tags=["auth"])

AnyStage = signed_in(*AuthStage)
Ready = signed_in(AuthStage.READY)
ReadyOrEnrolling = signed_in(
    AuthStage.READY, AuthStage.MFA_ENROLMENT_REQUIRED, AuthStage.TENANT_SELECTION
)
MfaPending = signed_in(AuthStage.MFA_PENDING)
Selecting = signed_in(AuthStage.READY, AuthStage.TENANT_SELECTION, AuthStage.MFA_ENROLMENT_REQUIRED)

NO_STORE = {"Cache-Control": "no-store"}


async def _limit(
    side: SideTransaction, settings: Settings, rule: ratelimit.Rule, subject: str
) -> None:
    """Spend a rate-limit token in its own transaction, so the spend survives a failed request."""
    async with side() as s:
        await ratelimit.hit(s, settings, rule, subject)


def _set_session_cookie(response: Response, settings: Settings, signed: SignedIn) -> None:
    response.set_cookie(
        session_cookie_name(settings),
        signed.token,
        max_age=settings.session_absolute_hours * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        session_cookie_name(settings),
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )


async def _state(db: DB, auth: AuthContext) -> SessionState:
    assert auth.user is not None
    assert auth.session is not None
    assert auth.stage is not None
    user = auth.user
    tenants = [
        TenantSummary(tenant_id=m.tenant_id, slug=m.tenant_slug, name=m.tenant_name)
        for m in await service.active_memberships(db, user.id)
    ]
    active = None
    permissions: dict[str, list[str]] = {}
    logo_version = None
    if auth.membership is not None:
        active = TenantSummary(
            tenant_id=auth.membership.tenant_id,
            slug=auth.membership.tenant_slug,
            name=auth.membership.tenant_name,
        )
        if auth.stage is AuthStage.READY and auth.principal is not None:
            grants = await load_grants(db, auth.principal)
            permissions = {
                code: sorted({s.type.value for s in scopes}) for code, scopes in grants.items()
            }
            logo_version = await logo_file_id(db, auth.membership.tenant_id)
    return SessionState(
        stage=auth.stage,
        user=UserSummary(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            mfa_enabled=user.mfa_enabled,
        ),
        active_tenant=active,
        tenants=tenants,
        permissions=permissions,
        logo_version=logo_version,
    )


async def _state_after(
    db: DB, settings: Settings, client: Client, signed: SignedIn
) -> SessionState:
    """Re-read the caller after a sign-in step, as their next request will see them."""
    return await _state(db, await authenticate_session(db, settings, client, signed.token))


# --- CSRF and login ---------------------------------------------------------------------


@router.get("/auth/csrf", response_model=CsrfToken, dependencies=[Depends(public)])
async def csrf_token(response: Response, settings: SettingsDep, request: Request) -> CsrfToken:
    """Issue (or re-use) the double-submit CSRF token. Call once before any POST."""
    token = request.cookies.get(csrf_cookie_name(settings)) or secrets.token_urlsafe(32)
    response.set_cookie(
        csrf_cookie_name(settings),
        token,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return CsrfToken(csrf_token=token)


@router.post(
    "/auth/login",
    response_model=SessionState,
    dependencies=[Depends(public)],
    responses={401: {"description": "Invalid credentials"}, 429: {"description": "Rate limited"}},
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    client: ClientDep,
    side: SideDep,
) -> SessionState:
    await _limit(side, settings, ratelimit.LOGIN_PER_IP, client.ip or "unknown")
    await _limit(side, settings, ratelimit.LOGIN_PER_EMAIL, body.email)
    signed = await service.login(db, side, settings, client, body.email, body.password)
    _set_session_cookie(response, settings, signed)
    response.headers.update(NO_STORE)
    return await _state_after(db, settings, client, signed)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(public)])
async def logout(response: Response, db: DB, settings: SettingsDep, auth: AuthDep) -> None:
    if auth.session is not None:
        await revoke(db, auth.session.id)
    _clear_session_cookie(response, settings)


# --- MFA ----------------------------------------------------------------------------------


@router.post("/auth/mfa/verify", response_model=SessionState)
async def verify_mfa(
    body: MfaVerifyRequest,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    kek: KekDep,
    client: ClientDep,
    auth: Annotated[AuthContext, Depends(MfaPending)],
    side: SideDep,
) -> SessionState:
    assert auth.session is not None
    if not body.code and not body.recovery_code:
        raise UnprocessableError("Enter a code or a recovery code.")
    await _limit(side, settings, ratelimit.MFA_PER_USER, str(auth.session.user_id))
    signed = await service.verify_second_factor(
        db, settings, kek, client, auth.session, code=body.code, recovery_code=body.recovery_code
    )
    _set_session_cookie(response, settings, signed)
    return await _state_after(db, settings, client, signed)


@router.post("/auth/mfa/setup", response_model=MfaSetupResponse)
async def start_mfa_setup(
    response: Response,
    db: DB,
    kek: KekDep,
    auth: Annotated[AuthContext, Depends(ReadyOrEnrolling)],
) -> MfaSetupResponse:
    assert auth.user is not None
    setup = await service.start_mfa_setup(db, kek, auth.user)
    response.headers.update(NO_STORE)
    return MfaSetupResponse(secret=setup.secret, otpauth_uri=setup.otpauth_uri)


@router.post("/auth/mfa/confirm", response_model=RecoveryCodes)
async def confirm_mfa_setup(
    body: MfaCodeRequest,
    response: Response,
    db: DB,
    settings: SettingsDep,
    kek: KekDep,
    client: ClientDep,
    auth: Annotated[AuthContext, Depends(ReadyOrEnrolling)],
) -> RecoveryCodes:
    assert auth.session is not None
    signed, codes = await service.confirm_mfa_setup(
        db, settings, kek, client, auth.session, body.code
    )
    _set_session_cookie(response, settings, signed)
    response.headers.update(NO_STORE)
    return RecoveryCodes(recovery_codes=codes)


@router.post("/auth/mfa/recovery-codes", response_model=RecoveryCodes)
async def regenerate_recovery_codes(
    body: MfaCodeRequest,
    response: Response,
    db: DB,
    kek: KekDep,
    auth: Annotated[AuthContext, Depends(Ready)],
) -> RecoveryCodes:
    assert auth.user is not None
    await service.check_current_totp(db, kek, auth.user.id, body.code)
    response.headers.update(NO_STORE)
    return RecoveryCodes(recovery_codes=await service.replace_recovery_codes(db, auth.user.id))


@router.post("/auth/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_mfa(
    body: MfaCodeRequest,
    db: DB,
    kek: KekDep,
    client: ClientDep,
    auth: Annotated[AuthContext, Depends(Ready)],
) -> None:
    assert auth.user is not None
    required = await service.any_membership_requires_mfa(db, auth.user.id)
    await service.disable_mfa(
        db, kek, client, auth.user.id, body.code, required_by_a_tenant=required
    )


# --- passwords and email -----------------------------------------------------------------


@router.post(
    "/auth/password/forgot", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(public)]
)
async def forgot_password(
    body: ForgotPasswordRequest,
    db: DB,
    settings: SettingsDep,
    kek: KekDep,
    client: ClientDep,
    background: BackgroundTasks,
    side: SideDep,
) -> dict[str, str]:
    """Always 202, whether or not the email has an account."""
    await _limit(side, settings, ratelimit.RESET_PER_IP, client.ip or "unknown")
    await _limit(side, settings, ratelimit.RESET_PER_EMAIL, body.email)
    queued = await service.request_password_reset(db, settings, kek, body.email)
    if queued is not None:
        # Background tasks run after the response, i.e. after the request's commit.
        background.add_task(dispatch, [queued])
    return {"status": "If that address has an account, we've sent a reset link."}


@router.post(
    "/auth/password/reset", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(public)]
)
async def reset_password(
    body: ResetPasswordRequest, db: DB, settings: SettingsDep, client: ClientDep, side: SideDep
) -> None:
    await _limit(side, settings, ratelimit.RESET_PER_IP, client.ip or "unknown")
    await service.reset_password(db, settings, client, body.token, body.password)


@router.post("/auth/password/change", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    response: Response,
    db: DB,
    settings: SettingsDep,
    client: ClientDep,
    auth: Annotated[AuthContext, Depends(Ready)],
) -> None:
    assert auth.session is not None
    signed = await service.change_password(
        db, settings, client, auth.session, body.current_password, body.new_password
    )
    _set_session_cookie(response, settings, signed)


@router.post(
    "/auth/email/verify", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(public)]
)
async def verify_email(body: TokenRequest, db: DB) -> None:
    await service.verify_email(db, body.token)


# --- invites --------------------------------------------------------------------------------


@router.get(
    "/auth/invites/{token}", response_model=InviteStatusResponse, dependencies=[Depends(public)]
)
async def inspect_invite(
    token: str, db: DB, settings: SettingsDep, client: ClientDep, side: SideDep
) -> InviteStatusResponse:
    await _limit(side, settings, ratelimit.LOOKUP_PER_IP, client.ip or "unknown")
    status_ = await service.inspect_invite(db, token)
    return InviteStatusResponse(valid=status_.valid, needs_password=status_.needs_password)


@router.post("/auth/invites/accept", response_model=SessionState, dependencies=[Depends(public)])
async def accept_invite(
    body: AcceptInviteRequest,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    client: ClientDep,
    side: SideDep,
) -> SessionState:
    await _limit(side, settings, ratelimit.LOOKUP_PER_IP, client.ip or "unknown")
    signed = await service.accept_invite(db, settings, client, body.token, body.password)
    _set_session_cookie(response, settings, signed)
    return await _state_after(db, settings, client, signed)


# --- me and tenant switching ------------------------------------------------------------------


@router.get("/me", response_model=SessionState, tags=["me"])
async def me(db: DB, auth: Annotated[AuthContext, Depends(AnyStage)]) -> SessionState:
    return await _state(db, auth)


@router.post("/session/tenant", response_model=SessionState, tags=["me"])
async def switch_tenant(
    body: SwitchTenantRequest,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    client: ClientDep,
    auth: Annotated[AuthContext, Depends(Selecting)],
) -> SessionState:
    assert auth.session is not None
    signed = await service.switch_tenant(db, settings, client, auth.session, body.tenant_id)
    _set_session_cookie(response, settings, signed)
    return await _state_after(db, settings, client, signed)


# --- single sign-on -----------------------------------------------------------------------


def get_http_client(request: Request) -> httpx.AsyncClient:
    http: httpx.AsyncClient = request.app.state.http_client
    return http


@router.get("/auth/sso/discover", response_model=SsoDiscovery, dependencies=[Depends(public)])
async def sso_discover(
    email: Annotated[str, Query(max_length=254)],
    db: DB,
    settings: SettingsDep,
    client: ClientDep,
    side: SideDep,
) -> SsoDiscovery:
    """Which single sign-on options cover this email's domain (ids only)."""
    await _limit(side, settings, ratelimit.LOOKUP_PER_IP, client.ip or "unknown")
    options = await sso.discover(db, email)
    return SsoDiscovery(
        options=[
            SsoOption(tenant_id=o.tenant_id, sso_config_id=o.sso_config_id, enforced=o.enforce_sso)
            for o in options
        ]
    )


@router.get(
    "/auth/sso/start",
    status_code=status.HTTP_303_SEE_OTHER,
    dependencies=[Depends(public)],
    response_class=RedirectResponse,
)
async def sso_start(
    tenant_id: uuid.UUID,
    sso_config_id: uuid.UUID,
    db: DB,
    settings: SettingsDep,
    client: ClientDep,
    side: SideDep,
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    login_hint: Annotated[str | None, Query(max_length=254)] = None,
) -> RedirectResponse:
    await _limit(side, settings, ratelimit.LOOKUP_PER_IP, client.ip or "unknown")
    config = await sso.load_config(db, client, tenant_id, sso_config_id)
    started = await sso.start(settings, http, config, login_hint)
    response = RedirectResponse(started.authorization_url, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        sso.STATE_COOKIE,
        started.state_cookie,
        max_age=sso.STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return response


@router.get(
    "/auth/sso/callback",
    status_code=status.HTTP_303_SEE_OTHER,
    dependencies=[Depends(public)],
    response_class=RedirectResponse,
)
async def sso_callback(
    request: Request,
    code: Annotated[str, Query(max_length=2048)],
    state: Annotated[str, Query(max_length=256)],
    db: DB,
    settings: SettingsDep,
    kek: KekDep,
    client: ClientDep,
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> RedirectResponse:
    payload = sso.read_state(settings, request.cookies.get(sso.STATE_COOKIE), state)
    signed = await sso.complete(db, settings, kek, http, client, payload, code)
    response = RedirectResponse(
        f"{settings.public_base_url}/", status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session_cookie(response, settings, signed)
    response.delete_cookie(
        sso.STATE_COOKIE, path="/", secure=settings.secure_cookies, httponly=True, samesite="lax"
    )
    return response
