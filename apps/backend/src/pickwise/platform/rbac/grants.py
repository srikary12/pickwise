# SPDX-License-Identifier: AGPL-3.0-only
"""Effective permissions of a principal: permission code → the scopes it holds it in.

Read once per request inside the caller's tenant session (RLS applies), so role
changes take effect on the next request.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.permissions import PERMISSIONS
from pickwise.platform.rbac.principal import Principal
from pickwise.platform.scopes import DataScope, ScopeType

Grants = dict[str, tuple[DataScope, ...]]

_MEMBER_GRANTS = text(
    """
    SELECT rp.permission_code, ra.scope_type, ra.scope_id
    FROM platform.role_assignments ra
    JOIN platform.roles r ON r.tenant_id = ra.tenant_id AND r.id = ra.role_id
    JOIN platform.role_permissions rp ON rp.tenant_id = ra.tenant_id AND rp.role_id = ra.role_id
    JOIN platform.memberships m ON m.tenant_id = ra.tenant_id AND m.id = ra.membership_id
    WHERE ra.membership_id = :membership_id
      AND m.status = 'active'
      AND r.archived_at IS NULL
      AND (ra.valid_during IS NULL OR ra.valid_during @> now())
    """
)


async def load_grants(session: AsyncSession, principal: Principal) -> Grants:
    if principal.api_key_id is not None:
        # API keys act tenant-wide, only for the permissions listed on the key.
        return {
            code: (DataScope(ScopeType.TENANT),)
            for code in principal.api_key_scopes
            if code in PERMISSIONS
        }
    if principal.membership_id is None:
        return {}
    rows = (await session.execute(_MEMBER_GRANTS, {"membership_id": principal.membership_id})).all()
    grants: dict[str, set[DataScope]] = {}
    for code, scope_type, scope_id in rows:
        scope_uuid = scope_id if isinstance(scope_id, uuid.UUID) else None
        grants.setdefault(str(code), set()).add(DataScope(ScopeType(scope_type), scope_uuid))
    return {
        code: tuple(sorted(s, key=lambda d: (d.type, str(d.scope_id))))
        for code, s in grants.items()
    }
