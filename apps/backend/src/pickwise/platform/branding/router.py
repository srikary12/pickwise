# SPDX-License-Identifier: AGPL-3.0-only
"""Tenant branding: read by every member, changed by ``platform.tenant.manage``."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from pickwise.platform import storage
from pickwise.platform.auth.dependencies import (
    DB,
    AuthContext,
    Authorized,
    SettingsDep,
    require,
    signed_in,
)
from pickwise.platform.auth.service import AuthStage
from pickwise.platform.branding import service
from pickwise.platform.branding.schemas import BrandingOut, LogoUpdate
from pickwise.platform.files import service as files
from pickwise.shared.errors import NotFoundError

router = APIRouter(tags=["branding"])

Member = Annotated[AuthContext, Depends(signed_in(AuthStage.READY))]
Manage = Annotated[Authorized, Depends(require("platform.tenant.manage"))]


async def branding_for(db: DB, tenant_id: uuid.UUID) -> BrandingOut:
    name: str = (
        await db.execute(text("SELECT name FROM platform.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one()
    return BrandingOut(name=name, logo_version=await service.logo_file_id(db, tenant_id))


@router.get("/v1/branding", response_model=BrandingOut)
async def get_branding(db: DB, auth: Member) -> BrandingOut:
    assert auth.principal is not None
    assert auth.principal.tenant_id is not None
    return await branding_for(db, auth.principal.tenant_id)


@router.get("/v1/branding/logo", response_class=RedirectResponse, status_code=302)
async def get_logo(
    db: DB,
    settings: SettingsDep,
    auth: Member,
    v: Annotated[str | None, Query(description="Cache-buster: logo_version")] = None,
) -> RedirectResponse:
    """Redirects to a short-lived presigned URL for the logo image."""
    assert auth.principal is not None
    assert auth.principal.tenant_id is not None
    file_id = await service.logo_file_id(db, auth.principal.tenant_id)
    file = await files.get_file(db, file_id) if file_id else None
    if file is None or file.scan_status != "clean" or file.purged_at is not None:
        raise NotFoundError("This organisation has no logo.")
    url = await storage.presign_download(
        settings,
        file.bucket,
        file.storage_key,
        filename=file.original_name,
        content_type=file.mime_type or "image/png",
        expires_in=settings.files_download_ttl_seconds,
    )
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, max-age=60"})


@router.put("/v1/admin/tenant/logo", response_model=BrandingOut)
async def set_logo(body: LogoUpdate, db: DB, settings: SettingsDep, auth: Manage) -> BrandingOut:
    """Use an uploaded, clean PNG or JPEG (uploaded with owner_entity_type 'tenant_logo')."""
    await service.set_logo(db, settings, auth, body.file_id, body.row_version)
    return await branding_for(db, auth.tenant_id)


@router.delete("/v1/admin/tenant/logo", response_model=BrandingOut, status_code=status.HTTP_200_OK)
async def remove_logo(
    db: DB, auth: Manage, row_version: Annotated[int, Query(description="Tenant row_version")]
) -> BrandingOut:
    await service.clear_logo(db, auth, row_version)
    return await branding_for(db, auth.tenant_id)
