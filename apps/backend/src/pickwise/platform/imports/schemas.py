# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ImportStatus = Literal[
    "pending", "validating", "validated", "committing", "committed", "failed", "cancelled"
]


class ColumnOut(BaseModel):
    name: str
    label: str
    required: bool
    description: str


class ImportTypeOut(BaseModel):
    key: str
    label: str
    description: str
    columns: list[ColumnOut]
    sample: dict[str, str]
    permitted: bool = Field(description="Whether the caller may import this type.")


class ImportCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"import_type": "employees", "file_id": "0198f2a0-6c1e-7a52-9d3e-5b1f2c8a4e10"}
            ]
        }
    )

    import_type: str = Field(pattern=r"^[a-z][a-z_]{1,62}$")
    file_id: uuid.UUID = Field(description="A clean CSV or XLSX uploaded through /v1/files")


class RowVersion(BaseModel):
    row_version: int = Field(ge=1)


class ImportOut(BaseModel):
    id: uuid.UUID
    import_type: str
    file_id: uuid.UUID
    status: ImportStatus
    stats: dict[str, Any] = Field(
        description="Progress and results: rows, valid_rows, error_rows, errors_total, "
        "committed_rows, and error/error_message when it failed."
    )
    has_error_file: bool
    created_at: datetime
    updated_at: datetime
    row_version: int


class ImportPage(BaseModel):
    items: list[ImportOut]
    next_cursor: uuid.UUID | None


class ErrorFileOut(BaseModel):
    url: str = Field(description="Short-lived download link for the CSV of row errors")
    expires_in: int
