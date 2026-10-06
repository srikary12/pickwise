# SPDX-License-Identifier: AGPL-3.0-only
"""Reveal a masked value (/v1/pii/reveal): permission-checked, rate limited and audited."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from pickwise.platform import audit, ratelimit
from pickwise.platform.auth.dependencies import (
    DB,
    AuthContext,
    Authorized,
    SettingsDep,
    SideDep,
    signed_in,
)
from pickwise.platform.auth.service import AuthStage
from pickwise.platform.rbac.grants import load_grants
from pickwise.platform.reveal.registry import REVEAL_FIELDS
from pickwise.platform.reveal.schemas import RevealOut, RevealRequest
from pickwise.shared.errors import ForbiddenError, NotFoundError

router = APIRouter(prefix="/v1/pii", tags=["privacy"])

Ready = Annotated[AuthContext, Depends(signed_in(AuthStage.READY))]


@router.post("/reveal", response_model=RevealOut)
async def reveal(
    body: RevealRequest,
    response: Response,
    db: DB,
    settings: SettingsDep,
    side: SideDep,
    auth: Ready,
) -> RevealOut:
    """Each reveal writes a ``pii.reveal`` audit event naming the field and record, never the
    value. The response is not cacheable."""
    assert auth.principal is not None
    assert auth.principal.user_id is not None
    field = REVEAL_FIELDS.get(body.entity_type, body.field)
    if field is None:
        raise NotFoundError("That value can't be revealed.")
    grants = await load_grants(db, auth.principal)
    scopes = grants.get(field.permission)
    if not scopes:
        raise ForbiddenError("You don't have permission to do that.", code="permission_denied")
    async with side() as s:
        await ratelimit.hit(s, settings, ratelimit.REVEAL_PER_USER, str(auth.principal.user_id))
    allowed = Authorized(auth.principal, field.permission, scopes, grants)
    value = await field.reveal(db, allowed, body.entity_id)
    await audit.record(db, "pii.reveal", field.entity, body.entity_id, {"field": body.field})
    response.headers["Cache-Control"] = "no-store"
    return RevealOut(value=value)
