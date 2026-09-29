# SPDX-License-Identifier: AGPL-3.0-only
"""Authentication flows: login and lockout, MFA, password reset, email verification,
invites and tenant switching.

Every function takes the request's database session. Pre-tenant steps read only the
global identity tables and the SECURITY DEFINER lookups; tenant rows are read after
``set_context`` has narrowed the transaction to the tenant.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit
from pickwise.platform.auth import mfa, passwords
from pickwise.platform.auth.sessions import SessionRecord, create_session, revoke_all, rotate
from pickwise.platform.auth.tokens import new_token, token_hash
from pickwise.platform.crypto import KeyEncryptionKey, load_platform_keyring
from pickwise.platform.notifications.email import QueuedEmail, queue_email
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import set_context
from pickwise.shared.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotAuthenticatedError,
    UnprocessableError,
)
from pickwise.shared.settings import Settings

MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
RESET_TTL = timedelta(minutes=30)
VERIFY_TTL = timedelta(hours=48)

# One message for every login failure, so responses don't reveal which emails exist.
INVALID_CREDENTIALS = "The email or password is incorrect, or the account is locked."


class AuthStage(StrEnum):
    """What a signed-in session still has to do before it can use the app."""

    MFA_PENDING = "mfa_pending"
    TENANT_SELECTION = "tenant_selection"
    MFA_ENROLMENT_REQUIRED = "mfa_enrolment_required"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: uuid.UUID
    email: str
    display_name: str
    status: str
    mfa_enabled: bool
    email_verified: bool


@dataclass(frozen=True, slots=True)
class ActiveMembership:
    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_name: str
    mfa_required: bool


@dataclass(frozen=True, slots=True)
class SignedIn:
    token: str
    session: SessionRecord


@dataclass(frozen=True, slots=True)
class Client:
    ip: str | None
    user_agent: str | None
    request_id: str | None


async def pre_tenant(db: AsyncSession, client: Client, *, user_id: uuid.UUID | None = None) -> None:
    await set_context(
        db,
        RequestContext(ActorType.USER, None, user_id, client.request_id, client.ip),
    )


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> UserRecord | None:
    row = (
        await db.execute(
            text(
                "SELECT id, email, display_name, status, mfa_enabled, "
                "email_verified_at IS NOT NULL FROM platform.users WHERE id = :id"
            ),
            {"id": user_id},
        )
    ).one_or_none()
    return UserRecord(*row) if row else None


async def active_memberships(db: AsyncSession, user_id: uuid.UUID) -> list[ActiveMembership]:
    rows = (
        await db.execute(
            text(
                "SELECT membership_id, tenant_id, tenant_slug, tenant_name "
                "FROM platform.list_memberships_for_user(:u) "
                "WHERE membership_status = 'active' AND tenant_status = 'active'"
            ),
            {"u": user_id},
        )
    ).all()
    return [ActiveMembership(r[0], r[1], r[2], r[3], mfa_required=False) for r in rows]


async def membership_in(
    db: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID, client: Client
) -> ActiveMembership | None:
    """The user's active membership in ``tenant_id``; narrows the transaction to it."""
    await set_context(
        db, RequestContext(ActorType.USER, tenant_id, user_id, client.request_id, client.ip)
    )
    row = (
        await db.execute(
            text(
                "SELECT m.id, t.id, t.slug, t.name, "
                "       coalesce((t.settings ->> 'mfa_required')::boolean, false) "
                "FROM platform.memberships m JOIN platform.tenants t ON t.id = m.tenant_id "
                "WHERE m.user_id = :u AND m.status = 'active' AND t.status = 'active'"
            ),
            {"u": user_id},
        )
    ).one_or_none()
    return ActiveMembership(*row) if row else None


def stage_for(
    user: UserRecord, session: SessionRecord, membership: ActiveMembership | None
) -> AuthStage:
    if user.mfa_enabled and not session.mfa_verified:
        return AuthStage.MFA_PENDING
    if membership is None:
        return AuthStage.TENANT_SELECTION
    if membership.mfa_required and not user.mfa_enabled:
        return AuthStage.MFA_ENROLMENT_REQUIRED
    return AuthStage.READY


# --- login ---------------------------------------------------------------------


async def login(
    db: AsyncSession,
    side: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    settings: Settings,
    client: Client,
    email: str,
    password: str,
) -> SignedIn:
    """``side`` opens a separately committed transaction: a failed login raises, which
    rolls the request back, but the failure counter and audit event must persist."""
    await pre_tenant(db, client)
    row = (
        await db.execute(
            text(
                "SELECT id, password_hash, status, mfa_enabled, locked_until > now() AS locked "
                "FROM platform.users WHERE email = :e"
            ),
            {"e": email.strip()},
        )
    ).one_or_none()

    if row is None:
        passwords.verify_password(None, password)  # same cost as a real check
        async with side() as s:
            await _platform_event(s, "login.failed", None, client)
        raise NotAuthenticatedError(INVALID_CREDENTIALS, code="invalid_credentials")

    ok = passwords.verify_password(row.password_hash, password)
    if not ok or row.locked or row.status != "active":
        # The counter update is a single atomic statement, so concurrent failures
        # can't under-count; it runs in the side transaction so it survives the 401.
        async with side() as s:
            if not ok and not row.locked:
                await s.execute(
                    text(
                        "UPDATE platform.users SET "
                        "  failed_login_count = CASE WHEN failed_login_count + 1 >= :max THEN 0 "
                        "                            ELSE failed_login_count + 1 END, "
                        "  locked_until = CASE WHEN failed_login_count + 1 >= :max "
                        "                      THEN now() + :lockout ELSE locked_until END "
                        "WHERE id = :id"
                    ),
                    {"id": row.id, "max": MAX_FAILED_LOGINS, "lockout": LOCKOUT},
                )
            await _platform_event(s, "login.failed", row.id, client)
        raise NotAuthenticatedError(INVALID_CREDENTIALS, code="invalid_credentials")

    await _enforce_sso(db, row.id, email)

    new_hash = (
        passwords.hash_password(password) if passwords.needs_rehash(row.password_hash) else None
    )
    await db.execute(
        text(
            "UPDATE platform.users SET failed_login_count = 0, locked_until = NULL, "
            "last_login_at = now(), password_hash = coalesce(:h, password_hash) WHERE id = :id"
        ),
        {"id": row.id, "h": new_hash},
    )
    memberships = await active_memberships(db, row.id)
    tenant_id = memberships[0].tenant_id if len(memberships) == 1 else None
    token, record = await create_session(
        db,
        settings,
        user_id=row.id,
        tenant_id=tenant_id,
        mfa_verified=False,
        ip=client.ip,
        user_agent=client.user_agent,
    )
    await _platform_event(db, "login.succeeded", row.id, client)
    return SignedIn(token, record)


async def _enforce_sso(db: AsyncSession, user_id: uuid.UUID, email: str) -> None:
    """A tenant with enforce_sso blocks password login for its own members on its domains."""
    domain = email.rsplit("@", 1)[-1].strip().lower()
    rows = (
        await db.execute(
            text(
                "SELECT s.tenant_id FROM platform.resolve_sso_by_domain(:d) s "
                "JOIN platform.list_memberships_for_user(:u) m ON m.tenant_id = s.tenant_id "
                "WHERE s.enforce_sso AND m.membership_status = 'active'"
            ),
            {"d": domain, "u": user_id},
        )
    ).all()
    if rows:
        raise ForbiddenError("Your organisation requires single sign-on.", code="sso_required")


async def _platform_event(
    db: AsyncSession, action: str, user_id: uuid.UUID | None, client: Client
) -> None:
    await db.execute(
        text("SELECT audit.log_platform_event(:a, :u, :r, CAST(:ip AS inet))"),
        {"a": action, "u": user_id, "r": client.request_id, "ip": client.ip},
    )


# --- MFA -------------------------------------------------------------------------


async def verify_second_factor(
    db: AsyncSession,
    settings: Settings,
    kek: KeyEncryptionKey,
    client: Client,
    session: SessionRecord,
    *,
    code: str | None,
    recovery_code: str | None,
) -> SignedIn:
    row = (
        await db.execute(
            text(
                "SELECT mfa_totp_secret_enc, mfa_last_used_step, mfa_enabled "
                "FROM platform.users WHERE id = :u FOR UPDATE"
            ),
            {"u": session.user_id},
        )
    ).one()
    if not row.mfa_enabled:
        raise ConflictError("Two-factor authentication isn't set up for this account.")
    if recovery_code:
        used = (
            await db.execute(
                text(
                    "UPDATE platform.mfa_recovery_codes SET used_at = now() "
                    "WHERE user_id = :u AND code_hash = :h AND used_at IS NULL RETURNING id"
                ),
                {"u": session.user_id, "h": mfa.recovery_code_hash(session.user_id, recovery_code)},
            )
        ).first()
        if used is None:
            raise NotAuthenticatedError("That recovery code isn't valid.", code="invalid_code")
        await _platform_event(db, "mfa.recovery_code_used", session.user_id, client)
    else:
        keyring = await load_platform_keyring(db, kek)
        secret = mfa.decrypt_secret(keyring, session.user_id, bytes(row.mfa_totp_secret_enc))
        step = mfa.matching_step(secret, code or "", last_used_step=row.mfa_last_used_step)
        if step is None:
            raise NotAuthenticatedError("That code isn't valid.", code="invalid_code")
        await db.execute(
            text("UPDATE platform.users SET mfa_last_used_step = :s WHERE id = :u"),
            {"s": step, "u": session.user_id},
        )
    token, record = await rotate(
        db,
        settings,
        session,
        tenant_id=session.active_tenant_id,
        mfa_verified=True,
        ip=client.ip,
        user_agent=client.user_agent,
    )
    return SignedIn(token, record)


@dataclass(frozen=True, slots=True)
class MfaSetup:
    secret: str
    otpauth_uri: str


async def start_mfa_setup(db: AsyncSession, kek: KeyEncryptionKey, user: UserRecord) -> MfaSetup:
    if user.mfa_enabled:
        raise ConflictError("Two-factor authentication is already on.")
    secret = mfa.new_secret()
    keyring = await load_platform_keyring(db, kek)
    await db.execute(
        text(
            "UPDATE platform.users SET mfa_totp_secret_enc = :s, mfa_last_used_step = NULL "
            "WHERE id = :u"
        ),
        {"s": mfa.encrypt_secret(keyring, user.id, secret), "u": user.id},
    )
    return MfaSetup(secret, mfa.provisioning_uri(secret, user.email))


async def confirm_mfa_setup(
    db: AsyncSession,
    settings: Settings,
    kek: KeyEncryptionKey,
    client: Client,
    session: SessionRecord,
    code: str,
) -> tuple[SignedIn, list[str]]:
    row = (
        await db.execute(
            text(
                "SELECT mfa_totp_secret_enc, mfa_enabled FROM platform.users "
                "WHERE id = :u FOR UPDATE"
            ),
            {"u": session.user_id},
        )
    ).one()
    if row.mfa_enabled:
        raise ConflictError("Two-factor authentication is already on.")
    if row.mfa_totp_secret_enc is None:
        raise ConflictError("Start the setup first.")
    keyring = await load_platform_keyring(db, kek)
    secret = mfa.decrypt_secret(keyring, session.user_id, bytes(row.mfa_totp_secret_enc))
    step = mfa.matching_step(secret, code, last_used_step=None)
    if step is None:
        raise UnprocessableError(
            "That code isn't valid. Check the time on your device and try again.",
            code="invalid_code",
        )
    await db.execute(
        text("UPDATE platform.users SET mfa_enabled = true, mfa_last_used_step = :s WHERE id = :u"),
        {"s": step, "u": session.user_id},
    )
    codes = await replace_recovery_codes(db, session.user_id)
    await _platform_event(db, "mfa.enabled", session.user_id, client)
    token, record = await rotate(
        db,
        settings,
        session,
        tenant_id=session.active_tenant_id,
        mfa_verified=True,
        ip=client.ip,
        user_agent=client.user_agent,
    )
    return SignedIn(token, record), codes


async def replace_recovery_codes(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    codes = mfa.new_recovery_codes()
    await db.execute(
        text("DELETE FROM platform.mfa_recovery_codes WHERE user_id = :u"), {"u": user_id}
    )
    for code in codes:
        await db.execute(
            text("INSERT INTO platform.mfa_recovery_codes (user_id, code_hash) VALUES (:u, :h)"),
            {"u": user_id, "h": mfa.recovery_code_hash(user_id, code)},
        )
    return codes


async def check_current_totp(
    db: AsyncSession, kek: KeyEncryptionKey, user_id: uuid.UUID, code: str
) -> None:
    """Step-up check for sensitive MFA changes."""
    row = (
        await db.execute(
            text(
                "SELECT mfa_totp_secret_enc, mfa_last_used_step FROM platform.users "
                "WHERE id = :u AND mfa_enabled FOR UPDATE"
            ),
            {"u": user_id},
        )
    ).one_or_none()
    if row is None:
        raise ConflictError("Two-factor authentication isn't on.")
    keyring = await load_platform_keyring(db, kek)
    secret = mfa.decrypt_secret(keyring, user_id, bytes(row.mfa_totp_secret_enc))
    step = mfa.matching_step(secret, code, last_used_step=row.mfa_last_used_step)
    if step is None:
        raise ForbiddenError("That code isn't valid.", code="invalid_code")
    await db.execute(
        text("UPDATE platform.users SET mfa_last_used_step = :s WHERE id = :u"),
        {"s": step, "u": user_id},
    )


async def disable_mfa(
    db: AsyncSession,
    kek: KeyEncryptionKey,
    client: Client,
    user_id: uuid.UUID,
    code: str,
    *,
    required_by_a_tenant: bool,
) -> None:
    if required_by_a_tenant:
        raise ForbiddenError(
            "One of your organisations requires two-factor authentication.",
            code="mfa_required_by_tenant",
        )
    await check_current_totp(db, kek, user_id, code)
    await db.execute(
        text(
            "UPDATE platform.users SET mfa_enabled = false, mfa_totp_secret_enc = NULL, "
            "mfa_last_used_step = NULL WHERE id = :u"
        ),
        {"u": user_id},
    )
    await db.execute(
        text("DELETE FROM platform.mfa_recovery_codes WHERE user_id = :u"), {"u": user_id}
    )
    await _platform_event(db, "mfa.disabled", user_id, client)


async def any_membership_requires_mfa(db: AsyncSession, user_id: uuid.UUID) -> bool:
    for m in await active_memberships(db, user_id):
        await set_context(db, RequestContext(ActorType.USER, m.tenant_id, user_id))
        required: bool | None = (
            await db.execute(
                text(
                    "SELECT coalesce((settings ->> 'mfa_required')::boolean, false) "
                    "FROM platform.tenants WHERE id = :t"
                ),
                {"t": m.tenant_id},
            )
        ).scalar_one_or_none()
        if required:
            return True
    return False


# --- single-use tokens ---------------------------------------------------------------


async def _consume(
    db: AsyncSession, purpose: str, token: str
) -> tuple[uuid.UUID | None, str | None, uuid.UUID | None]:
    row = (
        await db.execute(
            text(
                "UPDATE platform.auth_tokens SET used_at = now() "
                "WHERE token_hash = :h AND purpose = :p AND used_at IS NULL AND expires_at > now() "
                "RETURNING user_id, email, tenant_id"
            ),
            {"h": token_hash(token), "p": purpose},
        )
    ).one_or_none()
    if row is None:
        raise AppError("This link is invalid or has expired.", code="invalid_token")
    return row[0], row[1], row[2]


async def request_password_reset(
    db: AsyncSession, settings: Settings, kek: KeyEncryptionKey, email: str
) -> QueuedEmail | None:
    """Always looks the same to the caller, whether or not the email has an account."""
    user = (
        await db.execute(
            text(
                "SELECT id, email FROM platform.users "
                "WHERE email = :e AND status = 'active' AND password_hash IS NOT NULL"
            ),
            {"e": email.strip()},
        )
    ).one_or_none()
    if user is None:
        return None
    token = new_token()
    await db.execute(
        text(
            "INSERT INTO platform.auth_tokens (purpose, token_hash, user_id, email, expires_at) "
            "VALUES ('reset_password', :h, :u, :e, now() + :ttl)"
        ),
        {"h": token_hash(token), "u": user.id, "e": user.email, "ttl": RESET_TTL},
    )
    keyring = await load_platform_keyring(db, kek)
    return await queue_email(
        db,
        keyring,
        tenant_id=None,
        to_address=str(user.email),
        template_key="reset_password",
        variables={"expires_minutes": int(RESET_TTL.total_seconds() // 60)},
        secrets={"link": f"{settings.public_base_url}/reset-password?token={token}"},
    )


async def reset_password(
    db: AsyncSession, settings: Settings, client: Client, token: str, new_password: str
) -> None:
    user_id, email, _ = await _consume(db, "reset_password", token)
    assert user_id is not None  # guaranteed by the auth_tokens CHECK for this purpose
    await _set_new_password(db, settings, user_id, email, new_password)
    await revoke_all(db, user_id)
    await _platform_event(db, "password.reset", user_id, client)


async def _set_new_password(
    db: AsyncSession, settings: Settings, user_id: uuid.UUID, email: str | None, new_password: str
) -> None:
    try:
        passwords.check_policy(new_password, email=email)
    except passwords.PasswordPolicyError as exc:
        raise UnprocessableError(str(exc), code="weak_password") from exc
    if settings.breached_password_check and await passwords.is_breached(new_password):
        raise UnprocessableError(
            "This password has appeared in a data breach. Choose a different one.",
            code="breached_password",
        )
    await db.execute(
        text(
            "UPDATE platform.users SET password_hash = :h, failed_login_count = 0, "
            "locked_until = NULL WHERE id = :u"
        ),
        {"h": passwords.hash_password(new_password), "u": user_id},
    )


async def change_password(
    db: AsyncSession,
    settings: Settings,
    client: Client,
    session: SessionRecord,
    current_password: str,
    new_password: str,
) -> SignedIn:
    row = (
        await db.execute(
            text("SELECT email, password_hash FROM platform.users WHERE id = :u FOR UPDATE"),
            {"u": session.user_id},
        )
    ).one()
    if not passwords.verify_password(row.password_hash, current_password):
        raise ForbiddenError("Your current password is incorrect.", code="invalid_credentials")
    await _set_new_password(db, settings, session.user_id, row.email, new_password)
    await revoke_all(db, session.user_id, except_id=session.id)
    token, record = await rotate(
        db,
        settings,
        session,
        tenant_id=session.active_tenant_id,
        mfa_verified=session.mfa_verified,
        ip=client.ip,
        user_agent=client.user_agent,
    )
    await _platform_event(db, "password.changed", session.user_id, client)
    return SignedIn(token, record)


async def verify_email(db: AsyncSession, token: str) -> None:
    user_id, _, _ = await _consume(db, "verify_email", token)
    await db.execute(
        text(
            "UPDATE platform.users SET email_verified_at = coalesce(email_verified_at, now()) "
            "WHERE id = :u"
        ),
        {"u": user_id},
    )


# --- invites ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InviteStatus:
    valid: bool
    needs_password: bool


async def inspect_invite(db: AsyncSession, token: str) -> InviteStatus:
    row = (
        await db.execute(
            text("SELECT tenant_id, user_id, is_expired, is_used FROM platform.resolve_invite(:h)"),
            {"h": token_hash(token)},
        )
    ).one_or_none()
    if row is None or row.is_expired or row.is_used:
        return InviteStatus(False, False)
    has_password = (
        await db.execute(
            text("SELECT password_hash IS NOT NULL FROM platform.users WHERE id = :u"),
            {"u": row.user_id},
        )
    ).scalar_one_or_none()
    return InviteStatus(True, not has_password)


async def accept_invite(
    db: AsyncSession, settings: Settings, client: Client, token: str, password: str | None
) -> SignedIn:
    user_id, email, tenant_id = await _consume(db, "invite", token)
    if user_id is None or tenant_id is None:
        raise AppError("This link is invalid or has expired.", code="invalid_token")
    has_password: bool = (
        await db.execute(
            text("SELECT password_hash IS NOT NULL FROM platform.users WHERE id = :u FOR UPDATE"),
            {"u": user_id},
        )
    ).scalar_one()
    if not has_password:
        if not password:
            raise UnprocessableError(
                "Choose a password to finish joining.", code="password_required"
            )
        await _set_new_password(db, settings, user_id, email, password)
    await db.execute(
        text(
            "UPDATE platform.users SET email_verified_at = coalesce(email_verified_at, now()) "
            "WHERE id = :u"
        ),
        {"u": user_id},
    )
    await set_context(
        db, RequestContext(ActorType.USER, tenant_id, user_id, client.request_id, client.ip)
    )
    activated = (
        await db.execute(
            text(
                "UPDATE platform.memberships SET status = 'active', "
                "joined_at = coalesce(joined_at, now()) "
                "WHERE user_id = :u AND status = 'invited' RETURNING id"
            ),
            {"u": user_id},
        )
    ).first()
    if activated is None:
        raise ConflictError("This invitation is no longer valid.", code="invalid_token")
    await pre_tenant(db, client, user_id=user_id)
    token_value, record = await create_session(
        db,
        settings,
        user_id=user_id,
        tenant_id=tenant_id,
        mfa_verified=False,
        ip=client.ip,
        user_agent=client.user_agent,
    )
    await _platform_event(db, "invite.accepted", user_id, client)
    return SignedIn(token_value, record)


# --- tenant switching --------------------------------------------------------------------


async def switch_tenant(
    db: AsyncSession,
    settings: Settings,
    client: Client,
    session: SessionRecord,
    tenant_id: uuid.UUID,
) -> SignedIn:
    if session.sso_tenant_id is not None and session.sso_tenant_id != tenant_id:
        raise ForbiddenError(
            "You signed in with your organisation's single sign-on, which only covers that "
            "organisation. Sign in again to switch.",
            code="sso_session_pinned",
        )
    membership = await membership_in(db, session.user_id, tenant_id, client)
    if membership is None:
        raise ForbiddenError("You don't have access to that organisation.", code="no_membership")
    await audit.record(
        db, "session.tenant_switched", "platform.memberships", membership.membership_id
    )
    await pre_tenant(db, client, user_id=session.user_id)
    token, record = await rotate(
        db,
        settings,
        session,
        tenant_id=tenant_id,
        mfa_verified=session.mfa_verified,
        ip=client.ip,
        user_agent=client.user_agent,
    )
    return SignedIn(token, record)
