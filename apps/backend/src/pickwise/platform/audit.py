# SPDX-License-Identifier: AGPL-3.0-only
"""Semantic audit events written by the app (CLAUDE.md rule 15).

Row changes are captured by the database trigger; this records the events a
trigger can't see (login, role.grant, api_key.created, …). Ids only: never
personal data in ``details``.
"""

import uuid
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

_INSERT = text(
    """
    INSERT INTO audit.events (tenant_id, actor_user_id, actor_type, action,
                              entity_schema, entity_table, entity_id, changes, request_id, ip)
    VALUES (platform.current_tenant_id(), platform.current_user_id(),
            coalesce(NULLIF(current_setting('app.actor_type', true), ''), 'system'),
            :action, :entity_schema, :entity_table, :entity_id, :details,
            NULLIF(current_setting('app.request_id', true), ''),
            NULLIF(current_setting('app.client_ip', true), '')::inet)
    """
).bindparams(bindparam("details", type_=JSONB))


async def record(
    session: AsyncSession,
    action: str,
    entity: str | None = None,
    entity_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Record ``action`` for the current tenant; ``entity`` is 'schema.table'."""
    schema, _, table = (entity or "").partition(".")
    await session.execute(
        _INSERT,
        {
            "action": action,
            "entity_schema": schema or None,
            "entity_table": table or None,
            "entity_id": entity_id,
            "details": details,
        },
    )
