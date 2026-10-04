# SPDX-License-Identifier: AGPL-3.0-only
"""Upload slots, completion and download authorisation (ADR 0013).

Nothing is served unless ``scan_status = 'clean'``. A file the caller may not see is
reported as not found, so ids can't be probed.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit, storage
from pickwise.platform.auth.dependencies import Authorized
from pickwise.platform.files import types
from pickwise.shared.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from pickwise.shared.settings import Settings

CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")

_COLUMNS = (
    "id, storage_key, bucket, original_name, declared_mime_type, declared_size_bytes, "
    "mime_type, size_bytes, scan_status, scan_detail, classification, owner_entity_type, "
    "owner_entity_id, created_by, uploaded_at, upload_expires_at, scanned_at, purged_at, "
    "retention_until, row_version"
)


@dataclass(frozen=True, slots=True)
class FileRecord:
    id: uuid.UUID
    storage_key: str
    bucket: str
    original_name: str
    declared_mime_type: str | None
    declared_size_bytes: int | None
    mime_type: str | None
    size_bytes: int | None
    scan_status: str
    scan_detail: str | None
    classification: str
    owner_entity_type: str | None
    owner_entity_id: uuid.UUID | None
    created_by: uuid.UUID | None
    uploaded_at: datetime | None
    upload_expires_at: datetime | None
    scanned_at: datetime | None
    purged_at: datetime | None
    retention_until: datetime | None
    row_version: int


@dataclass(frozen=True, slots=True)
class UploadSlot:
    file: FileRecord
    url: str
    fields: dict[str, str]


class FileGoneError(AppError):
    status_code = 410
    code = "file_purged"


async def get_file(db: AsyncSession, file_id: uuid.UUID) -> FileRecord | None:
    row = (
        await db.execute(
            text(f"SELECT {_COLUMNS} FROM platform.files WHERE id = :id"),  # noqa: S608
            {"id": file_id},
        )
    ).one_or_none()
    return FileRecord(*row) if row else None


def _validate(settings: Settings, filename: str, mime_type: str, size: int) -> None:
    if mime_type not in settings.files_allowed_mime_types:
        raise UnprocessableError("That file type isn't allowed.", code="file_type_not_allowed")
    if types.extension_of(filename) not in types.EXTENSIONS.get(mime_type, frozenset()):
        raise UnprocessableError(
            "The file name doesn't match its type.", code="file_extension_mismatch"
        )
    if size > settings.files_max_bytes:
        raise UnprocessableError(
            f"Files can be at most {settings.files_max_bytes // (1024 * 1024)} MB.",
            code="file_too_large",
        )


async def create_upload(
    db: AsyncSession,
    settings: Settings,
    auth: Authorized,
    *,
    filename: str,
    mime_type: str,
    size_bytes: int,
    classification: str = "confidential",
    owner_entity_type: str | None = None,
    owner_entity_id: uuid.UUID | None = None,
) -> UploadSlot:
    _validate(settings, filename, mime_type, size_bytes)
    if classification not in CLASSIFICATIONS:
        raise UnprocessableError("Unknown classification.", code="invalid_classification")
    if (owner_entity_type is None) != (owner_entity_id is None):
        raise UnprocessableError(
            "owner_entity_type and owner_entity_id go together.", code="invalid_owner"
        )
    file_id = uuid.UUID(str((await db.execute(text("SELECT uuidv7()"))).scalar_one()))
    key = f"{auth.tenant_id}/{file_id}"
    row = (
        await db.execute(
            text(
                f"INSERT INTO platform.files (id, storage_key, bucket, original_name, "  # noqa: S608
                "  declared_mime_type, declared_size_bytes, classification, owner_entity_type, "
                "  owner_entity_id, upload_expires_at) "
                "VALUES (:id, :key, :bucket, :name, :mime, :size, :cls, :ot, :oi, "
                "  now() + make_interval(secs => :ttl)) "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "id": file_id,
                "key": key,
                "bucket": settings.s3_bucket_quarantine,
                "name": types.safe_name(filename),
                "mime": mime_type,
                "size": size_bytes,
                "cls": classification,
                "ot": owner_entity_type,
                "oi": owner_entity_id,
                "ttl": settings.files_upload_ttl_seconds,
            },
        )
    ).one()
    post = await storage.presign_upload(
        settings,
        key,
        content_type=mime_type,
        max_bytes=settings.files_max_bytes,
        expires_in=settings.files_upload_ttl_seconds,
    )
    return UploadSlot(FileRecord(*row), str(post["url"]), dict(post["fields"]))


def _is_uploader(auth: Authorized, file: FileRecord) -> bool:
    # Users own what they created; API keys act tenant-wide on what keys created.
    user = auth.principal.user_id
    return file.created_by is None if user is None else file.created_by == user


async def complete_upload(
    db: AsyncSession, settings: Settings, auth: Authorized, file_id: uuid.UUID
) -> tuple[FileRecord, bool]:
    """Confirm the object arrived. Returns the file and whether a scan must be queued."""
    file = await get_file(db, file_id)
    if file is None or not _is_uploader(auth, file):
        raise NotFoundError("File not found.")
    if file.uploaded_at is not None:
        return file, False
    if file.purged_at is not None or (
        file.upload_expires_at is not None
        and file.upload_expires_at < (await db.execute(text("SELECT now()"))).scalar_one()
    ):
        raise ConflictError("The upload window has closed. Start again.", code="upload_expired")
    async with storage.s3_client(settings) as s3:
        head: dict[str, Any] | None = await storage.head_object(s3, file.bucket, file.storage_key)
    if head is None:
        raise ConflictError("The file hasn't been uploaded yet.", code="upload_missing")
    size = int(head["ContentLength"])
    row = (
        await db.execute(
            text(
                f"UPDATE platform.files SET uploaded_at = now(), size_bytes = :size "  # noqa: S608
                f"WHERE id = :id RETURNING {_COLUMNS}"
            ),
            {"size": size, "id": file_id},
        )
    ).one()
    await audit.record(db, "file.uploaded", "platform.files", file_id, {"size_bytes": size})
    return FileRecord(*row), True


async def authorize_read(db: AsyncSession, auth: Authorized, file: FileRecord) -> None:
    """Raise NotFound unless the caller may see this file."""
    from pickwise.platform.files.access import FILE_ACCESS  # registry imports this module

    if _is_uploader(auth, file):
        return
    if "platform.files.read_all" in auth.grants:
        return
    rule = FILE_ACCESS.rule(file.owner_entity_type)
    if rule is not None and await rule(db, auth.principal, file):
        return
    raise NotFoundError("File not found.")


async def download_url(
    db: AsyncSession, settings: Settings, auth: Authorized, file_id: uuid.UUID
) -> tuple[str, FileRecord]:
    file = await get_file(db, file_id)
    if file is None:
        raise NotFoundError("File not found.")
    await authorize_read(db, auth, file)
    if file.purged_at is not None:
        raise FileGoneError("This file has been removed.")
    if file.scan_status == "pending":
        raise ConflictError("The file is still being checked.", code="file_not_ready")
    if file.scan_status != "clean":
        raise ConflictError("This file isn't available.", code="file_unavailable")
    url = await storage.presign_download(
        settings,
        file.bucket,
        file.storage_key,
        filename=file.original_name,
        content_type=file.mime_type or "application/octet-stream",
        expires_in=settings.files_download_ttl_seconds,
    )
    await audit.record(db, "file.download", "platform.files", file_id)
    return url, file
