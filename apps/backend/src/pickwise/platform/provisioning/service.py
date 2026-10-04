# SPDX-License-Identifier: AGPL-3.0-only
"""Create a tenant, its keys, defaults and first admin, in one ops transaction (ADR 0007)."""

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit
from pickwise.platform.auth.tokens import new_token, token_hash
from pickwise.platform.crypto import KeyEncryptionKey, load_tenant_keyring
from pickwise.platform.crypto.keys import BLIND_INDEX, DATA, tenant_key_aad, wrap_new_key
from pickwise.platform.notifications.email import QueuedEmail, queue_email
from pickwise.platform.provisioning import PROVISIONING_HOOKS
from pickwise.shared.settings import Settings

INVITE_TTL = timedelta(days=7)
_SLUG = re.compile(r"^[a-z0-9-]{3,40}$")


class ProvisioningError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    tenant_id: uuid.UUID
    admin_user_id: uuid.UUID
    admin_membership_id: uuid.UUID
    invite_email: QueuedEmail | None


async def _set_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    # Column defaults (tenant_id = current_tenant_id()) and audit rows use this.
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
    )


async def ensure_user(session: AsyncSession, email: str, display_name: str) -> uuid.UUID:
    row: uuid.UUID = (
        await session.execute(
            text(
                "WITH created AS (INSERT INTO platform.users (email, display_name) VALUES (:e, :n) "
                "ON CONFLICT (email) DO NOTHING RETURNING id) "
                "SELECT id FROM created UNION ALL SELECT id FROM platform.users WHERE email = :e "
                "LIMIT 1"
            ),
            {"e": email, "n": display_name},
        )
    ).scalar_one()
    return uuid.UUID(str(row))


async def add_member(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role_key: str,
    *,
    status: str = "invited",
    invited_by: uuid.UUID | None = None,
) -> uuid.UUID:
    membership_id: uuid.UUID = (
        await session.execute(
            text(
                "WITH created AS (INSERT INTO platform.memberships "
                "  (tenant_id, user_id, status, invited_by, invited_at, joined_at) "
                "  VALUES (:t, :u, :s, :by, now(), CASE WHEN :s = 'active' THEN now() END) "
                "  ON CONFLICT (tenant_id, user_id) DO NOTHING RETURNING id) "
                "SELECT id FROM created UNION ALL "
                "SELECT id FROM platform.memberships WHERE tenant_id = :t AND user_id = :u LIMIT 1"
            ),
            {"t": tenant_id, "u": user_id, "s": status, "by": invited_by},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO platform.role_assignments "
            "(tenant_id, membership_id, role_id, scope_type, granted_by) "
            "SELECT :t, :m, r.id, 'tenant', :by FROM platform.roles r "
            "WHERE r.tenant_id = :t AND r.key = :role "
            "AND NOT EXISTS (SELECT 1 FROM platform.role_assignments ra WHERE ra.tenant_id = :t "
            "  AND ra.membership_id = :m AND ra.role_id = r.id AND ra.scope_type = 'tenant')"
        ),
        {"t": tenant_id, "m": membership_id, "role": role_key, "by": invited_by},
    )
    return uuid.UUID(str(membership_id))


async def create_invite(
    session: AsyncSession,
    settings: Settings,
    kek: KeyEncryptionKey,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    email: str,
    tenant_name: str,
) -> QueuedEmail:
    token = new_token()
    await session.execute(
        text(
            "INSERT INTO platform.auth_tokens "
            "(purpose, token_hash, user_id, email, tenant_id, expires_at) "
            "VALUES ('invite', :h, :u, :e, :t, now() + :ttl)"
        ),
        {"h": token_hash(token), "u": user_id, "e": email, "t": tenant_id, "ttl": INVITE_TTL},
    )
    keyring = await load_tenant_keyring(session, kek, tenant_id)
    return await queue_email(
        session,
        keyring,
        tenant_id=tenant_id,
        to_address=email,
        template_key="invite",
        variables={"tenant_name": tenant_name, "expires_days": INVITE_TTL.days},
        secrets={"link": f"{settings.public_base_url}/invite/{token}"},
    )


async def provision_tenant(
    session: AsyncSession,
    settings: Settings,
    kek: KeyEncryptionKey,
    *,
    slug: str,
    name: str,
    admin_email: str,
    admin_name: str,
    tenant_settings: dict[str, object] | None = None,
    send_invite: bool = True,
) -> ProvisionedTenant:
    """Must run in an ops session: it creates rows before any tenant context exists."""
    slug = slug.strip().lower()
    if not _SLUG.match(slug):
        raise ProvisioningError("slug must be 3-40 characters of a-z, 0-9 and '-'")
    exists = (
        await session.execute(text("SELECT 1 FROM platform.tenants WHERE slug = :s"), {"s": slug})
    ).first()
    if exists:
        raise ProvisioningError(f"a tenant with slug {slug!r} already exists")

    tenant_id = uuid.UUID(
        str(
            (
                await session.execute(
                    text(
                        "INSERT INTO platform.tenants (slug, name, status, settings) "
                        "VALUES (:s, :n, 'provisioning', :settings) RETURNING id"
                    ).bindparams(bindparam("settings", type_=JSONB)),
                    {"s": slug, "n": name, "settings": tenant_settings or {}},
                )
            ).scalar_one()
        )
    )
    await _set_tenant(session, tenant_id)

    for purpose in (DATA, BLIND_INDEX):
        _material, wrapped = wrap_new_key(kek, tenant_key_aad(tenant_id, purpose, 1))
        await session.execute(
            text(
                "INSERT INTO platform.tenant_keys "
                "(tenant_id, purpose, version, wrapped_key, kek_id) "
                "VALUES (:t, :p, 1, :w, :k)"
            ),
            {"t": tenant_id, "p": purpose, "w": wrapped, "k": kek.kek_id},
        )

    await PROVISIONING_HOOKS.run_all(session, tenant_id)

    admin_user_id = await ensure_user(session, admin_email, admin_name)
    membership_id = await add_member(session, tenant_id, admin_user_id, "tenant_admin")
    invite = None
    if send_invite:
        invite = await create_invite(
            session,
            settings,
            kek,
            tenant_id=tenant_id,
            user_id=admin_user_id,
            email=admin_email,
            tenant_name=name,
        )
    await session.execute(
        text("UPDATE platform.tenants SET status = 'active' WHERE id = :t"), {"t": tenant_id}
    )
    await audit.record(session, "tenant.provisioned", "platform.tenants", tenant_id)
    return ProvisionedTenant(tenant_id, admin_user_id, membership_id, invite)


async def sync_defaults(session: AsyncSession) -> dict[uuid.UUID, list[str]]:
    """Re-run every provisioning hook for every non-closed tenant (ops session)."""
    tenants: list[uuid.UUID] = list(
        (
            await session.execute(
                text("SELECT id FROM platform.tenants WHERE status <> 'closed' ORDER BY id")
            )
        )
        .scalars()
        .all()
    )
    ran: dict[uuid.UUID, list[str]] = {}
    for tenant_id in tenants:
        await _set_tenant(session, tenant_id)
        ran[tenant_id] = await PROVISIONING_HOOKS.run_all(session, tenant_id)
    return ran
