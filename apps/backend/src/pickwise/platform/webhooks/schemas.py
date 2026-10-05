# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pickwise.platform.events.registry import SUBSCRIPTION_PATTERN

DeliveryStatus = Literal["pending", "succeeded", "failed", "dead"]
MAX_EVENT_TYPES = 50


def _clean_event_types(values: list[str]) -> list[str]:
    cleaned = list(dict.fromkeys(v.strip() for v in values))
    for value in cleaned:
        if not SUBSCRIPTION_PATTERN.match(value):
            raise ValueError(
                f"{value!r} isn't an event type; use e.g. 'leave.request.approved' or 'leave.*'"
            )
    return cleaned


class EndpointCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"url": "https://hooks.example.com/pickwise", "event_types": ["leave.*"]}]
        }
    )

    url: str = Field(min_length=8, max_length=2048)
    event_types: list[str] = Field(
        default_factory=list,
        max_length=MAX_EVENT_TYPES,
        description="Exact types or 'prefix.*'. Empty means every event.",
    )

    _check_types = field_validator("event_types")(_clean_event_types)


class EndpointUpdate(EndpointCreate):
    is_active: bool = True
    row_version: int = Field(ge=1)


class EndpointOut(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[str]
    is_active: bool
    created_at: datetime
    row_version: int


class EndpointWithSecret(EndpointOut):
    secret: str | None = Field(
        description=(
            "The signing secret. Shown only in this response, so store it now. "
            "Null when an Idempotency-Key replays the creation."
        )
    )


class SecretOut(BaseModel):
    secret: str = Field(
        description="The new signing secret, shown once. The old one stops working."
    )


class DeliveryOut(BaseModel):
    id: uuid.UUID
    endpoint_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    status: DeliveryStatus
    attempt: int
    response_code: int | None
    next_attempt_at: datetime | None
    last_error: str | None
    created_at: datetime


class DeliveryPage(BaseModel):
    items: list[DeliveryOut]
    next_cursor: uuid.UUID | None
