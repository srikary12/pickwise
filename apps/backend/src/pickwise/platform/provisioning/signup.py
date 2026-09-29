# SPDX-License-Identifier: AGPL-3.0-only
"""Self-serve tenant signup, behind SIGNUP_ENABLED (off by default; ADR 0007).

1. ``start``: record a signup request and email a verification link.
2. ``verify``: consume the link; the request becomes ``queued`` and the API defers
   the ``provision_signup`` ops task (the API role never provisions itself).
3. ``provision``: the worker creates the tenant and invites the requester as admin.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.tokens import new_token, token_hash
from pickwise.platform.crypto import KeyEncryptionKey, load_platform_keyring
from pickwise.platform.notifications.email import QueuedEmail, queue_email
from pickwise.platform.provisioning.service import ProvisioningError, provision_tenant
from pickwise.shared.errors import AppError, ConflictError, NotFoundError
from pickwise.shared.logging import get_logger
from pickwise.shared.settings import Settings

VERIFY_TTL = timedelta(hours=48)
log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SignupStatus:
    id: uuid.UUID
    status: str
    tenant_slug: str | None


async def start(
    db: AsyncSession, settings: Settings, kek: KeyEncryptionKey, *, email: str, slug: str, name: str
) -> tuple[uuid.UUID, QueuedEmail]:
    slug = slug.strip().lower()
    taken = (
        await db.execute(text("SELECT 1 FROM platform.resolve_tenant_by_slug(:s)"), {"s": slug})
    ).first()
    if taken:
        raise ConflictError("That workspace address is taken.", code="slug_taken")
    signup_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO platform.signup_requests (email, requested_slug, requested_name) "
                "VALUES (:e, :s, :n) RETURNING id"
            ),
            {"e": email, "s": slug, "n": name},
        )
    ).scalar_one()
    token = new_token()
    await db.execute(
        text(
            "INSERT INTO platform.auth_tokens (purpose, token_hash, email, expires_at) "
            "VALUES ('signup', :h, :e, now() + :ttl)"
        ),
        {"h": token_hash(token), "e": email, "ttl": VERIFY_TTL},
    )
    queued = await queue_email(
        db,
        await load_platform_keyring(db, kek),
        tenant_id=None,
        to_address=email,
        template_key="signup_verify",
        variables={
            "requested_slug": slug,
            "expires_hours": int(VERIFY_TTL.total_seconds() // 3600),
        },
        secrets={"link": f"{settings.public_base_url}/signup/verify?token={token}"},
    )
    return signup_id, queued


async def verify(db: AsyncSession, token: str) -> uuid.UUID:
    email: str | None = (
        await db.execute(
            text(
                "UPDATE platform.auth_tokens SET used_at = now() "
                "WHERE token_hash = :h AND purpose = 'signup' AND used_at IS NULL AND expires_at > "
                "now() "
                "RETURNING email"
            ),
            {"h": token_hash(token)},
        )
    ).scalar_one_or_none()
    if email is None:
        raise AppError("This link is invalid or has expired.", code="invalid_token")
    signup_id: uuid.UUID | None = (
        await db.execute(
            text(
                "UPDATE platform.signup_requests SET status = 'queued' WHERE id = ("
                "  SELECT id FROM platform.signup_requests WHERE email = :e "
                "  AND status = 'pending_verification' ORDER BY created_at DESC LIMIT 1) RETURNING "
                "id"
            ),
            {"e": email},
        )
    ).scalar_one_or_none()
    if signup_id is None:
        raise AppError("This link is invalid or has expired.", code="invalid_token")
    return signup_id


async def status(db: AsyncSession, signup_id: uuid.UUID) -> SignupStatus:
    row = (
        await db.execute(
            text("SELECT id, status, requested_slug FROM platform.signup_requests WHERE id = :id"),
            {"id": signup_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Signup not found.")
    return SignupStatus(row[0], row[1], str(row[2]) if row[1] == "completed" else None)


async def provision(
    db: AsyncSession, settings: Settings, kek: KeyEncryptionKey, signup_id: uuid.UUID
) -> QueuedEmail | None:
    """Ops session. Idempotent: only a ``queued`` request is provisioned."""
    row = (
        await db.execute(
            text(
                "UPDATE platform.signup_requests SET status = 'provisioning' "
                "WHERE id = :id AND status = 'queued' RETURNING email, requested_slug, "
                "requested_name"
            ),
            {"id": signup_id},
        )
    ).one_or_none()
    if row is None:
        return None
    try:
        result = await provision_tenant(
            db,
            settings,
            kek,
            slug=str(row.requested_slug),
            name=row.requested_name or str(row.requested_slug),
            admin_email=str(row.email),
            admin_name=str(row.email).split("@")[0],
        )
    except ProvisioningError:
        await db.execute(
            text(
                "UPDATE platform.signup_requests SET status = 'failed', error_code = 'slug_taken', "
                "completed_at = now() WHERE id = :id"
            ),
            {"id": signup_id},
        )
        log.info("signup failed", signup_id=str(signup_id), error="slug_taken")
        return None
    await db.execute(
        text(
            "UPDATE platform.signup_requests SET status = 'completed', tenant_id = :t, "
            "completed_at = now() "
            "WHERE id = :id"
        ),
        {"t": result.tenant_id, "id": signup_id},
    )
    return result.invite_email
