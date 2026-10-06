# SPDX-License-Identifier: AGPL-3.0-only
from pydantic import BaseModel

from pickwise.platform.approvals.schemas import InboxItem
from pickwise.platform.notifications.schemas import NotificationOut


class PendingApprovals(BaseModel):
    count: int
    items: list[InboxItem]


class RecentNotifications(BaseModel):
    unread: int
    items: list[NotificationOut]


class DashboardOut(BaseModel):
    """Everything the home page shows, in one request."""

    pending_approvals: PendingApprovals
    notifications: RecentNotifications
