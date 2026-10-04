# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal["employee", "candidate", "job", "application", "requisition"]
FieldType = Literal["text", "number", "date", "boolean", "select", "multiselect", "user", "file"]


class FieldOut(BaseModel):
    id: uuid.UUID
    entity_type: EntityType
    key: str
    label: str
    field_type: FieldType
    options: dict[str, Any]
    required: bool
    is_sensitive: bool
    position: int
    archived_at: datetime | None
    row_version: int


class FieldCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "entity_type": "employee",
                    "key": "t_shirt_size",
                    "label": "T-shirt size",
                    "field_type": "select",
                    "options": {
                        "choices": [
                            {"value": "m", "label": "Medium"},
                            {"value": "l", "label": "Large"},
                        ]
                    },
                    "required": False,
                }
            ]
        }
    )

    entity_type: EntityType
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    label: str = Field(min_length=1, max_length=200)
    field_type: FieldType
    options: dict[str, Any] = Field(default_factory=dict)
    required: bool = False
    is_sensitive: bool = False
    position: int = Field(default=0, ge=0, le=10_000)


class FieldUpdate(BaseModel):
    """The key and type can't change: stored values were validated against them."""

    label: str = Field(min_length=1, max_length=200)
    options: dict[str, Any] = Field(default_factory=dict)
    required: bool = False
    is_sensitive: bool = False
    position: int = Field(default=0, ge=0, le=10_000)
    row_version: int
