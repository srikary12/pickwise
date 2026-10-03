# SPDX-License-Identifier: AGPL-3.0-only
"""The permission catalog, defined in code and synced to platform.permissions on
every migrate.

Each module registers its own permissions (and which system roles hold them) at
import time; platform registers its own below. ``pickwise.bootstrap`` imports every
module so the catalog is complete wherever it's read.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Permission:
    code: str
    module: str
    description: str
    is_sensitive: bool = False
    # System roles that hold this permission by default (tenant-level scope unless
    # the role's assignment narrows it).
    default_roles: tuple[str, ...] = field(default=())


class PermissionRegistry:
    def __init__(self) -> None:
        self._by_code: dict[str, Permission] = {}

    def register(self, *permissions: Permission) -> None:
        for p in permissions:
            existing = self._by_code.get(p.code)
            if existing is not None and existing != p:
                raise ValueError(f"permission {p.code} registered twice with different definitions")
            self._by_code[p.code] = p

    def get(self, code: str) -> Permission:
        return self._by_code[code]

    def __contains__(self, code: object) -> bool:
        return code in self._by_code

    def all(self) -> tuple[Permission, ...]:
        return tuple(sorted(self._by_code.values(), key=lambda p: p.code))


PERMISSIONS = PermissionRegistry()

TENANT_ADMIN = "tenant_admin"
HR_ADMIN = "hr_admin"

PERMISSIONS.register(
    Permission(
        "platform.users.read",
        "platform",
        "List users and their memberships and roles",
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "platform.users.invite",
        "platform",
        "Invite people to the tenant",
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "platform.users.manage",
        "platform",
        "Suspend or remove memberships",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN,),
    ),
    Permission(
        "platform.roles.read",
        "platform",
        "View roles and their permissions",
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "platform.roles.manage",
        "platform",
        "Create and edit roles, grant and revoke role assignments",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN,),
    ),
    Permission(
        "platform.api_keys.manage",
        "platform",
        "Create and revoke API keys",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN,),
    ),
    Permission(
        "platform.sso.manage",
        "platform",
        "Configure single sign-on",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN,),
    ),
    Permission(
        "platform.tenant.manage",
        "platform",
        "Change tenant settings such as MFA enforcement",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN,),
    ),
)
