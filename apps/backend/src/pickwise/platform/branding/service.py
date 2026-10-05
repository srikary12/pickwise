# SPDX-License-Identifier: AGPL-3.0-only
"""The tenant logo. The file id lives in ``platform.tenants.settings.logo_file_id`` (ADR 0020):
no foreign key, so every write validates the file here."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit
from pickwise.platform.auth.dependencies import Authorized
from pickwise.platform.files import service as files
from pickwise.shared.errors import ConflictError, NotFoundError, UnprocessableError
from pickwise.shared.settings import Settings

OWNER_TYPE = "tenant_logo"
LOGO_MIME_TYPES = ("image/png", "image/jpeg")  # never SVG: it can carry script


async def logo_file_id(db: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID | None:
    value = (
        await db.execute(
            text("SELECT settings->>'logo_file_id' FROM platform.tenants WHERE id = :t"),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    return uuid.UUID(value) if value else None


async def set_logo(
    db: AsyncSession, settings: Settings, auth: Authorized, file_id: uuid.UUID, row_version: int
) -> None:
    file = await files.get_file(db, file_id)
    if file is None or file.created_by != auth.principal.user_id:
        raise NotFoundError("File not found.")
    if file.owner_entity_type != OWNER_TYPE:
        raise UnprocessableError("That file wasn't uploaded as a logo.", code="invalid_logo")
    if file.scan_status == "pending":
        raise ConflictError("The file is still being checked.", code="file_not_ready")
    if file.scan_status != "clean" or file.purged_at is not None:
        raise UnprocessableError("That file isn't available.", code="file_unavailable")
    if file.mime_type not in LOGO_MIME_TYPES:
        raise UnprocessableError("A logo must be a PNG or JPEG image.", code="invalid_logo")
    if (file.size_bytes or 0) > settings.branding_logo_max_bytes:
        limit_kb = settings.branding_logo_max_bytes // 1024
        raise UnprocessableError(f"A logo can be at most {limit_kb} KB.", code="logo_too_large")
    await _write(db, auth.tenant_id, str(file_id), row_version)
    changes = {"logo_file_id": str(file_id)}
    await audit.record(db, "tenant.logo_changed", "platform.tenants", auth.tenant_id, changes)


async def clear_logo(db: AsyncSession, auth: Authorized, row_version: int) -> None:
    await _write(db, auth.tenant_id, None, row_version)
    await audit.record(db, "tenant.logo_removed", "platform.tenants", auth.tenant_id, {})


async def _write(db: AsyncSession, tenant_id: uuid.UUID, value: str | None, version: int) -> None:
    updated = (
        await db.execute(
            text(
                "UPDATE platform.tenants SET settings = CASE WHEN CAST(:v AS text) IS NULL "
                "THEN settings - 'logo_file_id' "
                "ELSE settings || jsonb_build_object('logo_file_id', CAST(:v AS text)) END "
                "WHERE id = :t AND row_version = :version RETURNING id"
            ),
            {"v": value, "t": tenant_id, "version": version},
        )
    ).first()
    if updated is None:
        raise ConflictError(
            "Someone else changed this since you loaded it. Reload and try again.",
            code="stale_row_version",
        )
