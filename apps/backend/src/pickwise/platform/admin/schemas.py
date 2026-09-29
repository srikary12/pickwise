# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response models for tenant administration."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pickwise.platform.auth.schemas import EmailStr
from pickwise.platform.scopes import ScopeType

RoleKey = str


class PermissionOut(BaseModel):
    code: str
    module: str
    description: str
    is_sensitive: bool


class RoleAssignmentOut(BaseModel):
    id: uuid.UUID
    role_id: uuid.UUID
    role_key: str
    scope_type: ScopeType
    scope_id: uuid.UUID | None


class MemberOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "membership_id": "01928f6e-…",
                    "user_id": "01928f6e-…",
                    "email": "admin@acme.test",
                    "display_name": "Acme Admin",
                    "status": "active",
                    "mfa_enabled": True,
                    "row_version": 3,
                    "roles": [
                        {
                            "id": "01928f6e-…",
                            "role_id": "01928f6e-…",
                            "role_key": "tenant_admin",
                            "scope_type": "tenant",
                            "scope_id": None,
                        }
                    ],
                }
            ]
        }
    )

    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: str
    status: str
    mfa_enabled: bool
    row_version: int
    roles: list[RoleAssignmentOut]


class InviteRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"email": "new.hire@acme.test", "display_name": "New Hire", "role_key": "employee"}
            ]
        }
    )

    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    role_key: RoleKey = Field(default="employee", pattern=r"^[a-z][a-z0-9_]{1,62}$")


class MembershipUpdate(BaseModel):
    status: Literal["active", "suspended", "removed"]
    row_version: int = Field(description="The row_version you last read; a stale one returns 409")


class RoleOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]
    row_version: int


class RoleCreate(BaseModel):
    key: RoleKey = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    permissions: list[str] = Field(default_factory=list, max_length=500)


class RolePermissionsUpdate(BaseModel):
    permissions: list[str] = Field(max_length=500)
    row_version: int


class RoleAssignmentCreate(BaseModel):
    membership_id: uuid.UUID
    role_id: uuid.UUID
    scope_type: ScopeType = ScopeType.TENANT
    scope_id: uuid.UUID | None = None


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    key: str | None = Field(
        description="The key itself. Shown only in the first response; null on an idempotent "
        "replay."
    )


class SsoConfigOut(BaseModel):
    id: uuid.UUID
    issuer: str
    client_id: str
    allowed_domains: list[str]
    enforce_sso: bool
    jit_provisioning: bool
    default_role_key: str | None
    row_version: int


class SsoConfigWrite(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "issuer": "https://login.microsoftonline.com/<tenant-id>/v2.0",
                    "client_id": "…",
                    "client_secret": "…",
                    "allowed_domains": ["acme.com"],
                    "enforce_sso": False,
                    "jit_provisioning": True,
                    "default_role_key": "employee",
                }
            ]
        }
    )

    issuer: str = Field(pattern=r"^https://", max_length=500)
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str | None = Field(
        default=None,
        max_length=2000,
        description="Write-only. Omit on update to keep the current secret.",
    )
    allowed_domains: list[str] = Field(min_length=1, max_length=50)
    enforce_sso: bool = False
    jit_provisioning: bool = False
    default_role_key: str | None = None
    row_version: int | None = Field(default=None, description="Required on update")


class TenantSettingsOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    mfa_required: bool
    row_version: int


class TenantSettingsUpdate(BaseModel):
    mfa_required: bool
    row_version: int
