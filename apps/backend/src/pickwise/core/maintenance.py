# SPDX-License-Identifier: AGPL-3.0-only
"""Nightly upkeep of derived core data."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.core import hierarchy
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database


async def rebuild_tenant_hierarchy(database: Database, tenant_id: uuid.UUID) -> int:
    """Recompute one tenant's reporting closure from its current job records and re-sync the
    automatic manager grants. Applies future-dated manager changes whose day has come, and heals
    any drift. Idempotent."""
    async with database.tenant_session(
        RequestContext(ActorType.WORKER, tenant_id, None, None, None)
    ) as session:
        rows = await hierarchy.rebuild(session)
        await hierarchy.sync_manager_roles(session)
    return rows


async def active_tenant_ids(session: AsyncSession) -> list[uuid.UUID]:
    rows = (
        await session.execute(text("SELECT id FROM platform.tenants WHERE status = 'active'"))
    ).all()
    return [r[0] for r in rows]
