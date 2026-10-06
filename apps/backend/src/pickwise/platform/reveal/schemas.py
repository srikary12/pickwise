# SPDX-License-Identifier: AGPL-3.0-only
import uuid

from pydantic import BaseModel, ConfigDict, Field


class RevealRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"entity_type": "employee", "entity_id": "01928f6e-…", "field": "pan"}]
        }
    )

    entity_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_]+$")
    entity_id: uuid.UUID
    field: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_]+$")


class RevealOut(BaseModel):
    value: str | None = Field(description="Shown once; never cached. Null when the field is empty")
