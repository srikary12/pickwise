# SPDX-License-Identifier: AGPL-3.0-only
"""Core HR permissions. Registered when ``wiring.register_all`` imports this module."""

from pickwise.platform.permissions import (
    EVERY_ROLE,
    HR_ADMIN,
    PERMISSIONS,
    TENANT_ADMIN,
    Permission,
)

PERMISSIONS.register(
    Permission(
        "core.org.read",
        "core",
        "View legal entities, locations, departments, designations and grades",
        default_roles=EVERY_ROLE,
    ),
    Permission(
        "core.org.manage",
        "core",
        "Create and change the organisation structure",
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
)
