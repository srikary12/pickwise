# SPDX-License-Identifier: AGPL-3.0-only
"""Platform's provisioning hook: system roles, their grants, number sequences.

Idempotent: inserts only what's missing, so `tenant sync-defaults` can re-run it
and pick up permissions that later modules add to the system roles.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.provisioning import PROVISIONING_HOOKS
from pickwise.platform.rbac.system_roles import SYSTEM_ROLES, default_grants

NUMBER_SEQUENCES = (("employee_code", "EMP"), ("requisition", "REQ"), ("offer", "OFR"))


async def seed_platform_defaults(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    for role in SYSTEM_ROLES:
        await session.execute(
            text(
                "INSERT INTO platform.roles (tenant_id, key, name, description, is_system) "
                "VALUES (:t, :key, :name, :description, true) "
                "ON CONFLICT (tenant_id, key) DO NOTHING"
            ),
            {"t": tenant_id, "key": role.key, "name": role.name, "description": role.description},
        )
        for code in default_grants(role.key):
            await session.execute(
                text(
                    "INSERT INTO platform.role_permissions (tenant_id, role_id, permission_code) "
                    "SELECT :t, r.id, :code FROM platform.roles r "
                    "WHERE r.tenant_id = :t AND r.key = :key "
                    "ON CONFLICT DO NOTHING"
                ),
                {"t": tenant_id, "key": role.key, "code": code},
            )
    for key, prefix in NUMBER_SEQUENCES:
        await session.execute(
            text(
                "INSERT INTO platform.number_sequences (tenant_id, key, prefix) "
                "VALUES (:t, :k, :p) "
                "ON CONFLICT (tenant_id, key) DO NOTHING"
            ),
            {"t": tenant_id, "k": key, "p": prefix},
        )


PROVISIONING_HOOKS.register("platform", "defaults", seed_platform_defaults)
