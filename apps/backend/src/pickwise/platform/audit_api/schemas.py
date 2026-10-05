# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_user_id: uuid.UUID | None
    actor_name: str | None
    actor_type: str
    action: str
    entity_schema: str | None
    entity_table: str | None
    entity_id: uuid.UUID | None
    changes: dict[str, Any] | None = Field(
        description="Column diffs; confidential and restricted columns show only '[changed]'."
    )
    request_id: str | None
    ip: str | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None
