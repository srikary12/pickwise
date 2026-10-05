# SPDX-License-Identifier: AGPL-3.0-only
"""Platform's own approver resolvers: ``role:<key>`` and ``user:<id>``.

Registered when ``wiring.register_all`` imports this module. ``manager``,
``skip_level`` and ``dept_head`` need org data, so core registers them in Phase 5.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.approvals.registry import APPROVER_RESOLVERS, ResolveContext


async def resolve_role(db: AsyncSession, ctx: ResolveContext) -> list[uuid.UUID]:
    """Members holding the role at tenant scope. A role limited to one department or
    location says nothing about who may approve an arbitrary request, so it doesn't count."""
    if ctx.argument is None:
        return []
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT m.user_id FROM platform.role_assignments ra "
                "JOIN platform.roles r ON r.tenant_id = ra.tenant_id AND r.id = ra.role_id "
                "JOIN platform.memberships m "
                "  ON m.tenant_id = ra.tenant_id AND m.id = ra.membership_id "
                "WHERE r.key = :key AND r.archived_at IS NULL AND ra.scope_type = 'tenant' "
                "  AND (ra.valid_during IS NULL OR ra.valid_during @> now()) "
                "ORDER BY m.user_id"
            ),
            {"key": ctx.argument},
        )
    ).all()
    return [r[0] for r in rows]


async def resolve_user(db: AsyncSession, ctx: ResolveContext) -> list[uuid.UUID]:
    if ctx.argument is None:
        return []
    try:
        return [uuid.UUID(ctx.argument)]
    except ValueError:
        return []


APPROVER_RESOLVERS.register("role", resolve_role)
APPROVER_RESOLVERS.register("user", resolve_user)
