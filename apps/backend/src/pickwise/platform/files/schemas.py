# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Classification = Literal["public", "internal", "confidential", "restricted"]
ScanStatus = Literal["pending", "clean", "infected", "error"]


class UploadRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "filename": "offer-letter.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 182044,
                    "classification": "confidential",
                }
            ]
        }
    )

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=100)
    size_bytes: int = Field(gt=0, description="Announced size; the store enforces the cap")
    classification: Classification = "confidential"
    owner_entity_type: str | None = Field(default=None, max_length=64)
    owner_entity_id: uuid.UUID | None = None


class FileOut(BaseModel):
    id: uuid.UUID
    original_name: str
    mime_type: str | None
    size_bytes: int | None
    scan_status: ScanStatus
    scan_detail: str | None
    classification: Classification
    owner_entity_type: str | None
    owner_entity_id: uuid.UUID | None
    uploaded_at: datetime | None
    scanned_at: datetime | None


class UploadSlotOut(BaseModel):
    file: FileOut
    upload_url: str = Field(description="POST the form here: the fields first, then the file")
    upload_fields: dict[str, str]
    expires_at: datetime | None


class DownloadOut(BaseModel):
    url: str = Field(description="Short-lived; forces a download")
    expires_in: int
