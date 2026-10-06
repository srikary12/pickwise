# SPDX-License-Identifier: AGPL-3.0-only
"""One request for the home page: your waiting approvals and latest notifications."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from pickwise.platform.approvals import queries
from pickwise.platform.approvals.schemas import InboxItem
from pickwise.platform.auth.dependencies import DB, AuthContext, signed_in
from pickwise.platform.auth.service import AuthStage
from pickwise.platform.dashboard.schemas import (
    DashboardOut,
    PendingApprovals,
    RecentNotifications,
)
from pickwise.platform.notifications.schemas import NotificationOut

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])

Ready = Annotated[AuthContext, Depends(signed_in(AuthStage.READY))]

SHOWN = 5
_NOTIFICATION_COLUMNS = "id, type, title, body, link, entity_type, entity_id, read_at, created_at"


@router.get("", response_model=DashboardOut)
async def dashboard(db: DB, auth: Ready) -> DashboardOut:
    """Both lists are strictly the caller's own."""
    assert auth.user is not None  # signed_in guarantees a user
    user_id = auth.user.id
    tasks, _ = await queries.inbox(db, user_id, state="pending", before=None, limit=SHOWN)
    rows = (
        await db.execute(
            text(
                f"SELECT {_NOTIFICATION_COLUMNS} FROM platform.notifications "  # noqa: S608
                "WHERE user_id = :u ORDER BY id DESC LIMIT :n"
            ),
            {"u": user_id, "n": SHOWN},
        )
    ).all()
    unread: int = (
        await db.execute(
            text(
                "SELECT count(*) FROM platform.notifications WHERE user_id = :u AND read_at IS NULL"
            ),
            {"u": user_id},
        )
    ).scalar_one()
    return DashboardOut(
        pending_approvals=PendingApprovals(
            count=await queries.pending_count(db, user_id),
            items=[InboxItem(**t) for t in tasks],
        ),
        notifications=RecentNotifications(
            unread=unread,
            items=[
                NotificationOut(**dict(zip(NotificationOut.model_fields, r, strict=True)))
                for r in rows
            ],
        ),
    )
