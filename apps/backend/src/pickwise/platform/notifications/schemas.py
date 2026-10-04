# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class NotificationOut(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    body: str | None
    link: str | None
    entity_type: str | None
    entity_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    next_cursor: uuid.UUID | None = Field(
        default=None, description="Pass as `before` to fetch the next (older) page"
    )


class UnreadCount(BaseModel):
    unread: int


class ChannelPreference(BaseModel):
    in_app: bool
    email: bool


class PreferenceOut(BaseModel):
    type: str
    label: str
    description: str
    channels: ChannelPreference
    locked: list[str] = Field(description="Channels that can't be turned off")


class PreferencesUpdate(BaseModel):
    preferences: dict[str, ChannelPreference] = Field(
        description="Notification type key → channels. Unknown types are rejected."
    )
