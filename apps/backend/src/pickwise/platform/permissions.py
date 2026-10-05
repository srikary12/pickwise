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
EVERY_ROLE = (
    "tenant_admin",
    "hr_admin",
    "hr_ops",
    "payroll_admin",
    "recruiter",
    "hiring_manager",
    "interviewer",
    "manager",
    "employee",
)

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
    Permission(
        "platform.search.use",
        "platform",
        "Use global search (results are limited to what each record type's own permission allows)",
        default_roles=EVERY_ROLE,
    ),
    Permission(
        "platform.files.upload",
        "platform",
        "Upload files (scanned before use)",
        default_roles=EVERY_ROLE,
    ),
    Permission(
        "platform.files.read",
        "platform",
        "Download files you own or that a module lets you see",
        default_roles=EVERY_ROLE,
    ),
    Permission(
        "platform.files.read_all",
        "platform",
        "Download any clean file in the tenant",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "platform.custom_fields.read",
        "platform",
        "See custom-field definitions (to build forms)",
        default_roles=EVERY_ROLE,
    ),
    Permission(
        "platform.custom_fields.manage",
        "platform",
        "Create, edit and archive custom fields",
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "platform.custom_fields.read_sensitive",
        "platform",
        "See and edit values of custom fields marked sensitive",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "platform.webhooks.manage",
        "platform",
        "Create webhook endpoints, see deliveries and replay them",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN,),
    ),
    Permission(
        "platform.approvals.act",
        "platform",
        "Decide approval tasks assigned to you, start delegations for yourself",
        default_roles=EVERY_ROLE,
    ),
    Permission(
        "platform.approvals.manage",
        "platform",
        "Edit approval policies; see, delegate and cancel any approval",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "platform.imports.run",
        "platform",
        "Upload bulk-import files, run dry runs and see your own imports",
        default_roles=(TENANT_ADMIN, HR_ADMIN, "hr_ops"),
    ),
    Permission(
        "platform.imports.read_all",
        "platform",
        "See every import in the tenant, including its error file",
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "platform.audit.read",
        "platform",
        "Search the audit trail",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "platform.audit.export",
        "platform",
        "Download the audit trail as CSV",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN,),
    ),
)
