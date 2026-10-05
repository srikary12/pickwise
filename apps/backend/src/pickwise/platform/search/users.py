# SPDX-License-Identifier: AGPL-3.0-only
"""Search provider for people with a sign-in. Needs platform.users.read, within its scope."""

import uuid

from sqlalchemy import column, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.dependencies import Authorized
from pickwise.platform.scopes import ScopeTarget, scope_filter
from pickwise.platform.search.registry import SEARCH_PROVIDERS, SearchHit, SearchProvider

_users = table("users", column("id"), column("email"), column("display_name"), schema="platform")
_memberships = table("memberships", column("user_id"), column("status"), schema="platform")


def like_pattern(query: str) -> str:
    """A contains-pattern with the user's own ``%``, ``_`` and ``\\`` taken literally."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_users(
    session: AsyncSession, auth: Authorized, query: str, limit: int
) -> list[SearchHit]:
    visible = scope_filter(auth.scopes, auth.principal, ScopeTarget(user_column=_users.c.id))
    pattern = like_pattern(query)
    rows = (
        await session.execute(
            select(_users.c.id, _users.c.display_name, _users.c.email)
            .select_from(_memberships.join(_users, _users.c.id == _memberships.c.user_id))
            .where(
                visible,
                _memberships.c.status != "removed",
                _users.c.display_name.ilike(pattern, escape="\\")
                | _users.c.email.ilike(pattern, escape="\\"),
            )
            .order_by(_users.c.display_name)
            .limit(limit)
        )
    ).all()
    return [
        SearchHit(
            kind="user",
            id=uuid.UUID(str(r[0])),
            title=str(r[1]),
            subtitle=str(r[2]),
            link="/admin/users",
        )
        for r in rows
    ]


SEARCH_PROVIDERS.register(
    SearchProvider(
        kind="user",
        label="People with a sign-in",
        permission="platform.users.read",
        search=search_users,
    )
)
