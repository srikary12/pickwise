# SPDX-License-Identifier: AGPL-3.0-only
"""The signed-in user's own notifications (/v1/notifications). Strictly self-scoped."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import text

from pickwise.platform.auth.dependencies import DB, AuthContext, signed_in
from pickwise.platform.auth.service import AuthStage
from pickwise.platform.notifications import service
from pickwise.platform.notifications.registry import NOTIFICATION_TYPES
from pickwise.platform.notifications.schemas import (
    ChannelPreference,
    NotificationOut,
    NotificationPage,
    PreferenceOut,
    PreferencesUpdate,
    UnreadCount,
)
from pickwise.shared.errors import NotFoundError

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])

Ready = Annotated[AuthContext, Depends(signed_in(AuthStage.READY))]

_COLUMNS = "id, type, title, body, link, entity_type, entity_id, read_at, created_at"


def _user(auth: AuthContext) -> uuid.UUID:
    assert auth.user is not None  # signed_in guarantees a user
    return auth.user.id


@router.get("", response_model=NotificationPage)
async def list_notifications(
    db: DB,
    auth: Ready,
    unread: Annotated[bool, Query()] = False,
    before: Annotated[uuid.UUID | None, Query(description="Cursor: id from next_cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> NotificationPage:
    rows = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM platform.notifications "  # noqa: S608
                "WHERE user_id = :u AND (NOT :unread OR read_at IS NULL) "
                "  AND (CAST(:before AS uuid) IS NULL OR id < :before) "
                "ORDER BY id DESC LIMIT :n"
            ),
            {"u": _user(auth), "unread": unread, "before": before, "n": limit + 1},
        )
    ).all()
    items = [
        NotificationOut(**dict(zip(NotificationOut.model_fields, r, strict=True))) for r in rows
    ]
    more = len(items) > limit
    return NotificationPage(items=items[:limit], next_cursor=items[limit - 1].id if more else None)


@router.get("/unread-count", response_model=UnreadCount)
async def unread_count(db: DB, auth: Ready) -> UnreadCount:
    count: int = (
        await db.execute(
            text(
                "SELECT count(*) FROM platform.notifications WHERE user_id = :u AND read_at IS NULL"
            ),
            {"u": _user(auth)},
        )
    ).scalar_one()
    return UnreadCount(unread=count)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all(db: DB, auth: Ready) -> None:
    await db.execute(
        text(
            "UPDATE platform.notifications SET read_at = now() "
            "WHERE user_id = :u AND read_at IS NULL"
        ),
        {"u": _user(auth)},
    )


@router.get("/preferences", response_model=list[PreferenceOut])
async def get_preferences(db: DB, auth: Ready) -> list[PreferenceOut]:
    saved = await service.own_preferences(db, _user(auth))
    out = []
    for ntype in NOTIFICATION_TYPES.all():
        channels = service.effective_channels(ntype, saved)
        out.append(
            PreferenceOut(
                type=ntype.key,
                label=ntype.label,
                description=ntype.description,
                channels=ChannelPreference(in_app="in_app" in channels, email="email" in channels),
                locked=list(ntype.locked),
            )
        )
    return out


@router.put("/preferences", status_code=status.HTTP_204_NO_CONTENT)
async def put_preferences(body: PreferencesUpdate, db: DB, auth: Ready) -> None:
    await service.save_preferences(
        db, _user(auth), {k: v.model_dump() for k, v in body.preferences.items()}
    )


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: uuid.UUID, db: DB, auth: Ready, response: Response) -> None:
    result = await db.execute(
        text(
            "UPDATE platform.notifications SET read_at = coalesce(read_at, now()) "
            "WHERE id = :id AND user_id = :u"
        ),
        {"id": notification_id, "u": _user(auth)},
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise NotFoundError("Notification not found.")
