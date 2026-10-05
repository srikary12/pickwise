# SPDX-License-Identifier: AGPL-3.0-only
import uuid

from pydantic import BaseModel, ConfigDict, Field


class BrandingOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"name": "Acme Industries", "logo_version": "01928f6e-…"}]}
    )

    name: str
    logo_version: uuid.UUID | None = Field(
        description="Changes whenever the logo does; fetch the image from /v1/branding/logo?v=…"
    )


class LogoUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"file_id": "01928f6e-…", "row_version": 3}]}
    )

    file_id: uuid.UUID = Field(description="A clean upload with owner_entity_type 'tenant_logo'")
    row_version: int
