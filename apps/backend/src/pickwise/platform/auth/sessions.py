# SPDX-License-Identifier: AGPL-3.0-only
"""Server-side sessions (CLAUDE.md "Auth"; ADR 0010).

The browser holds a random 32-byte token in an httpOnly cookie; the database holds
only its SHA-256. Sessions have an idle timeout and an absolute lifetime, and are
rotated (new token, old one revoked) on login, MFA verification, tenant switch and
password change.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.tokens import new_token, token_hash
from pickwise.shared.settings import Settings

# last_seen_at is written at most this often, to keep reads cheap.
TOUCH_INTERVAL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: uuid.UUID
    user_id: uuid.UUID
    active_tenant_id: uuid.UUID | None
    mfa_verified: bool
    created_at: datetime
    # Set when the session came from a tenant's SSO: it may only act in that tenant.
    sso_tenant_id: uuid.UUID | None = None


def session_cookie_name(settings: Settings) -> str:
    # __Host- cookies must be Secure, Path=/ and have no Domain: the browser then
    # refuses any attempt by a sibling subdomain to set or shadow them.
    return "__Host-pw_session" if settings.secure_cookies else "pw_session"


def csrf_cookie_name(settings: Settings) -> str:
    return "__Host-pw_csrf" if settings.secure_cookies else "pw_csrf"


async def create_session(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    mfa_verified: bool,
    ip: str | None,
    user_agent: str | None,
    sso_tenant_id: uuid.UUID | None = None,
) -> tuple[str, SessionRecord]:
    token = new_token()
    row = (
        await db.execute(
            text(
                "INSERT INTO platform.sessions "
                "(user_id, active_tenant_id, token_hash, mfa_verified, ip, user_agent, expires_at, "
                " sso_tenant_id) "
                "VALUES (:u, :t, :h, :mfa, CAST(:ip AS inet), :ua, "
                "        now() + make_interval(hours => :hours), :sso) "
                "RETURNING id, user_id, active_tenant_id, mfa_verified, created_at, sso_tenant_id"
            ),
            {
                "u": user_id,
                "t": tenant_id,
                "h": token_hash(token),
                "mfa": mfa_verified,
                "ip": ip,
                "ua": (user_agent or "")[:512] or None,
                "hours": settings.session_absolute_hours,
                "sso": sso_tenant_id,
            },
        )
    ).one()
    return token, SessionRecord(*row)


async def load_session(db: AsyncSession, settings: Settings, token: str) -> SessionRecord | None:
    """The live session for a cookie token, or None if unknown, revoked, idle or expired."""
    row = (
        await db.execute(
            text(
                "SELECT id, user_id, active_tenant_id, mfa_verified, created_at, sso_tenant_id, "
                "       last_seen_at < now() - make_interval(secs => :touch) AS stale "
                "FROM platform.sessions "
                "WHERE token_hash = :h AND revoked_at IS NULL AND expires_at > now() "
                "  AND last_seen_at > now() - make_interval(mins => :idle)"
            ),
            {
                "h": token_hash(token),
                "idle": settings.session_idle_minutes,
                "touch": TOUCH_INTERVAL_SECONDS,
            },
        )
    ).one_or_none()
    if row is None:
        return None
    if row.stale:
        await db.execute(
            text("UPDATE platform.sessions SET last_seen_at = now() WHERE id = :id"), {"id": row.id}
        )
    return SessionRecord(
        row.id,
        row.user_id,
        row.active_tenant_id,
        row.mfa_verified,
        row.created_at,
        row.sso_tenant_id,
    )


async def revoke(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(
        text(
            "UPDATE platform.sessions SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL"
        ),
        {"id": session_id},
    )


async def revoke_all(
    db: AsyncSession, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None
) -> int:
    result = await db.execute(
        text(
            "UPDATE platform.sessions SET revoked_at = now() "
            "WHERE user_id = :u AND revoked_at IS NULL AND id IS DISTINCT FROM :keep"
        ),
        {"u": user_id, "keep": except_id},
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def rotate(
    db: AsyncSession,
    settings: Settings,
    current: SessionRecord,
    *,
    tenant_id: uuid.UUID | None,
    mfa_verified: bool,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, SessionRecord]:
    """Replace a session with a fresh token (fixation defence) and the given state."""
    await revoke(db, current.id)
    return await create_session(
        db,
        settings,
        user_id=current.user_id,
        tenant_id=tenant_id,
        mfa_verified=mfa_verified,
        ip=ip,
        user_agent=user_agent,
        sso_tenant_id=current.sso_tenant_id,
    )
