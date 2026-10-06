# SPDX-License-Identifier: AGPL-3.0-only
"""Global search (/v1/search): every registered provider the caller may use, grouped by kind."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from pickwise.platform import ratelimit
from pickwise.platform.auth.dependencies import (
    DB,
    Authorized,
    SettingsDep,
    SideDep,
    require,
)
from pickwise.platform.search.registry import SEARCH_PROVIDERS
from pickwise.platform.search.schemas import GroupOut, HitOut, SearchOut

router = APIRouter(prefix="/v1/search", tags=["search"])

Use = Annotated[Authorized, Depends(require("platform.search.use"))]

PER_KIND = 5


@router.get("", response_model=SearchOut)
async def search(
    db: DB,
    auth: Use,
    settings: SettingsDep,
    side: SideDep,
    q: Annotated[str, Query(min_length=2, max_length=100, description="At least 2 characters")],
    kinds: Annotated[list[str] | None, Query(description="Limit to these kinds")] = None,
    limit: Annotated[int, Query(ge=1, le=10, description="Hits per kind")] = PER_KIND,
) -> SearchOut:
    """Hits are permission- and scope-filtered by each provider; nothing is cached."""
    caller = auth.principal.user_id or auth.principal.api_key_id
    async with side() as s:
        await ratelimit.hit(s, settings, ratelimit.SEARCH_PER_USER, str(caller))
    groups: list[GroupOut] = []
    for provider in SEARCH_PROVIDERS.all():
        if kinds and provider.kind not in kinds:
            continue
        scopes = auth.grants.get(provider.permission)
        if not scopes:
            continue
        allowed = Authorized(auth.principal, provider.permission, scopes, auth.grants)
        hits = await provider.search(db, allowed, q.strip(), limit)
        if hits:
            groups.append(
                GroupOut(
                    kind=provider.kind,
                    label=provider.label,
                    hits=[
                        HitOut(
                            kind=h.kind, id=h.id, title=h.title, subtitle=h.subtitle, link=h.link
                        )
                        for h in hits
                    ],
                )
            )
    return SearchOut(groups=groups)
