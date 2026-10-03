# SPDX-License-Identifier: AGPL-3.0-only
"""Scoped API keys for integrations (DATA_MODEL platform.api_keys, ADR 0004).

Format: ``pw_<8-char prefix>_<secret>``. Only the SHA-256 of the whole key is
stored, and the key is shown once. A request authenticates with
``Authorization: Bearer <key>``; the tenant is found through the SECURITY DEFINER
lookup ``platform.resolve_api_key`` before any tenant context exists.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit
from pickwise.platform.auth.service import Client
from pickwise.platform.auth.tokens import token_hash
from pickwise.platform.permissions import PERMISSIONS
from pickwise.platform.rbac.principal import Principal
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import set_context
from pickwise.shared.errors import NotFoundError, UnprocessableError

PREFIX = "pw_"


@dataclass(frozen=True, slots=True)
class CreatedKey:
    id: uuid.UUID
    prefix: str
    key: str


def _new_key() -> tuple[str, str]:
    prefix = secrets.token_hex(4)
    return prefix, f"{PREFIX}{prefix}_{secrets.token_urlsafe(32)}"


async def authenticate_api_key(db: AsyncSession, key: str, client: Client) -> Principal | None:
    if not key.startswith(PREFIX):
        return None
    row = (
        await db.execute(
            text(
                "SELECT tenant_id, api_key_id, scopes, is_usable FROM platform.resolve_api_key(:h)"
            ),
            {"h": token_hash(key)},
        )
    ).one_or_none()
    if row is None or not row.is_usable:
        return None
    await set_context(
        db,
        RequestContext(ActorType.API_KEY, row.tenant_id, None, client.request_id, client.ip),
    )
    await db.execute(
        text(
            "UPDATE platform.api_keys SET last_used_at = now() WHERE id = :id "
            "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')"
        ),
        {"id": row.api_key_id},
    )
    return Principal(
        ActorType.API_KEY,
        row.tenant_id,
        api_key_id=row.api_key_id,
        api_key_scopes=tuple(row.scopes or ()),
    )


async def create_api_key(
    db: AsyncSession,
    *,
    name: str,
    scopes: list[str],
    expires_at: datetime | None,
    grantable: set[str],
) -> CreatedKey:
    """``grantable`` is what the creator holds: a key can't have more than its creator."""
    unknown = sorted(set(scopes) - {p.code for p in PERMISSIONS.all()})
    if unknown:
        raise UnprocessableError(
            f"Unknown permissions: {', '.join(unknown)}", code="unknown_permission"
        )
    excess = sorted(set(scopes) - grantable)
    if excess:
        raise UnprocessableError(
            f"You can't grant permissions you don't hold: {', '.join(excess)}",
            code="permission_denied",
        )
    prefix, key = _new_key()
    key_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO platform.api_keys (name, prefix, key_hash, scopes, expires_at) "
                "VALUES (:n, :p, :h, :s, :e) RETURNING id"
            ),
            {
                "n": name,
                "p": prefix,
                "h": token_hash(key),
                "s": sorted(set(scopes)),
                "e": expires_at,
            },
        )
    ).scalar_one()
    await audit.record(db, "api_key.created", "platform.api_keys", key_id)
    return CreatedKey(key_id, prefix, key)


async def revoke_api_key(db: AsyncSession, key_id: uuid.UUID) -> None:
    result = await db.execute(
        text(
            "UPDATE platform.api_keys SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL"
        ),
        {"id": key_id},
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise NotFoundError("API key not found or already revoked.")
    await audit.record(db, "api_key.revoked", "platform.api_keys", key_id)
