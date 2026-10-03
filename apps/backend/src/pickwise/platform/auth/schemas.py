# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response models for authentication and the current user."""

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from pickwise.platform.auth.service import AuthStage

# A deliberately loose check (full validation would need another dependency); the
# address is confirmed by the email we send to it.
EmailStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]


class CsrfToken(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"csrf_token": "3q2-9Xc…"}]})

    csrf_token: str = Field(description="Send back in the X-CSRF-Token header on unsafe requests")


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"email": "admin@acme.test", "password": "correct horse battery"}]
        }
    )

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TenantSummary(BaseModel):
    tenant_id: uuid.UUID
    slug: str
    name: str


class SessionState(BaseModel):
    """Where a signed-in session stands; the web app routes on ``stage``."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "stage": "ready",
                    "user": {
                        "id": "01928f6e-…",
                        "email": "admin@acme.test",
                        "display_name": "Acme Admin",
                        "mfa_enabled": True,
                    },
                    "active_tenant": {
                        "tenant_id": "01928f6e-…",
                        "slug": "acme",
                        "name": "Acme Industries",
                    },
                    "tenants": [
                        {"tenant_id": "01928f6e-…", "slug": "acme", "name": "Acme Industries"}
                    ],
                    "permissions": {"platform.users.read": ["tenant"]},
                }
            ]
        }
    )

    stage: AuthStage
    user: "UserSummary"
    active_tenant: TenantSummary | None
    tenants: list[TenantSummary]
    permissions: dict[str, list[str]] = Field(
        default_factory=dict, description="Effective permissions and the scope types they apply in"
    )


class UserSummary(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    mfa_enabled: bool


class MfaVerifyRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"code": "123456"}]})

    code: str | None = Field(default=None, pattern=r"^\s*\d{3}\s?\d{3}\s*$")
    recovery_code: str | None = Field(default=None, max_length=32)


class MfaSetupResponse(BaseModel):
    secret: str = Field(description="Base32 TOTP seed, for manual entry")
    otpauth_uri: str = Field(description="Render as a QR code for authenticator apps")


class MfaCodeRequest(BaseModel):
    code: str = Field(pattern=r"^\s*\d{3}\s?\d{3}\s*$")


class RecoveryCodes(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"recovery_codes": ["abcd-efgh-ijkl-mnop"]}]}
    )

    recovery_codes: list[str] = Field(description="Shown once. Each works one time.")


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=128)


class InviteStatusResponse(BaseModel):
    valid: bool
    needs_password: bool = Field(description="The invitee has no password yet and must choose one")


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=20, max_length=128)
    password: str | None = Field(default=None, max_length=256)


class SwitchTenantRequest(BaseModel):
    tenant_id: uuid.UUID


class SsoOption(BaseModel):
    tenant_id: uuid.UUID
    sso_config_id: uuid.UUID
    enforced: bool = Field(
        description="Password sign-in is disabled for this organisation's members"
    )


class SsoDiscovery(BaseModel):
    options: list[SsoOption]


SessionState.model_rebuild()
