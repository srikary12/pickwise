# SPDX-License-Identifier: AGPL-3.0-only
"""The API side of imports: create, inspect, commit, cancel. The work itself is in
``processing`` (worker)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit, storage
from pickwise.platform.auth.dependencies import Authorized
from pickwise.platform.files import service as files
from pickwise.platform.files.types import XLSX
from pickwise.platform.imports.registry import IMPORT_TYPES, ImportType
from pickwise.shared.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from pickwise.shared.settings import Settings

READ_ALL = "platform.imports.read_all"
_COLUMNS = (
    "id, import_type, file_id, status, stats, error_file_id, created_by, created_at, "
    "updated_at, row_version"
)
COMMITTABLE = ("validated",)


@dataclass(frozen=True, slots=True)
class ImportRecord:
    id: uuid.UUID
    import_type: str
    file_id: uuid.UUID
    status: str
    stats: dict[str, Any]
    error_file_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    row_version: int


def _is_owner(auth: Authorized, record: ImportRecord) -> bool:
    user = auth.principal.user_id
    return record.created_by is None if user is None else record.created_by == user


def type_for(auth: Authorized, key: str) -> ImportType:
    import_type = IMPORT_TYPES.get(key)
    if import_type is None:
        raise UnprocessableError("Unknown import type.", code="unknown_import_type")
    if import_type.permission not in auth.grants:
        raise ForbiddenError("You don't have permission to do that.", code="permission_denied")
    return import_type


async def get_import(db: AsyncSession, auth: Authorized, import_id: uuid.UUID) -> ImportRecord:
    row = (
        await db.execute(
            text(f"SELECT {_COLUMNS} FROM platform.imports WHERE id = :id"),  # noqa: S608
            {"id": import_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Import not found.")
    record = ImportRecord(*row)
    if not _is_owner(auth, record) and READ_ALL not in auth.grants:
        raise NotFoundError("Import not found.")
    return record


async def list_imports(
    db: AsyncSession, auth: Authorized, *, before: uuid.UUID | None, limit: int
) -> tuple[list[ImportRecord], uuid.UUID | None]:
    user = auth.principal.user_id
    rows = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM platform.imports "  # noqa: S608
                "WHERE (:all OR created_by IS NOT DISTINCT FROM CAST(:u AS uuid)) "
                "  AND (CAST(:before AS uuid) IS NULL OR id < :before) "
                "ORDER BY id DESC LIMIT :n"
            ),
            {"all": READ_ALL in auth.grants, "u": user, "before": before, "n": limit + 1},
        )
    ).all()
    records = [ImportRecord(*r) for r in rows]
    more = len(records) > limit
    return records[:limit], records[limit - 1].id if more else None


async def create_import(
    db: AsyncSession, auth: Authorized, *, import_type: str, file_id: uuid.UUID
) -> ImportRecord:
    type_for(auth, import_type)
    file = await files.get_file(db, file_id)
    if file is None:
        raise NotFoundError("File not found.")
    await files.authorize_read(db, auth, file)
    if file.purged_at is not None:
        raise ConflictError("That file has been removed.", code="file_purged")
    if file.scan_status != "clean":
        raise ConflictError("The file hasn't passed the virus scan yet.", code="file_not_ready")
    # CSV has no magic bytes: the pipeline sniffs it as text, so a text file declared as CSV counts.
    is_csv = file.mime_type in ("text/csv", "text/plain") and file.declared_mime_type == "text/csv"
    if file.mime_type != XLSX and not is_csv:
        raise UnprocessableError("Import a CSV or XLSX file.", code="file_type_not_allowed")
    row = (
        await db.execute(
            text(
                "INSERT INTO platform.imports (import_type, file_id) "  # noqa: S608
                f"VALUES (:t, :f) RETURNING {_COLUMNS}"
            ),
            {"t": import_type, "f": file_id},
        )
    ).one()
    record = ImportRecord(*row)
    await audit.record(
        db,
        "import.created",
        "platform.imports",
        record.id,
        {"import_type": import_type, "file_id": str(file_id)},
    )
    return record


async def request_commit(
    db: AsyncSession, auth: Authorized, import_id: uuid.UUID, *, row_version: int
) -> ImportRecord:
    """Move a clean dry run to ``committing`` (the worker does the writing). A failed commit
    may be requested again: committers are idempotent."""
    record = await get_import(db, auth, import_id)
    type_for(auth, record.import_type)
    if record.row_version != row_version:
        raise ConflictError("This import changed. Reload and try again.", code="stale_row_version")
    retry = record.status == "failed" and record.stats.get("phase") == "commit"
    if record.status not in COMMITTABLE and not retry:
        raise ConflictError("Only a checked import can be committed.", code="import_not_validated")
    if record.stats.get("error_rows", 0):
        raise ConflictError(
            "The dry run found errors. Fix the file and import it again.",
            code="import_has_errors",
        )
    current = await files.get_file(db, record.file_id)
    sha = None if current is None or current.purged_at is not None else await _sha(db, record)
    if sha is None or sha != record.stats.get("file_sha256"):
        raise ConflictError(
            "The file changed since the dry run. Import it again.", code="file_changed"
        )
    row = (
        await db.execute(
            text(
                "UPDATE platform.imports SET status = 'committing', dry_run = false, "  # noqa: S608
                "stats = stats || jsonb_build_object('phase', 'commit', 'committed_rows', 0) "
                f"WHERE id = :id AND row_version = :v RETURNING {_COLUMNS}"
            ),
            {"id": import_id, "v": row_version},
        )
    ).one_or_none()
    if row is None:
        raise ConflictError("This import changed. Reload and try again.", code="stale_row_version")
    await audit.record(
        db,
        "import.commit_requested",
        "platform.imports",
        import_id,
        {"import_type": record.import_type},
    )
    return ImportRecord(*row)


async def _sha(db: AsyncSession, record: ImportRecord) -> str | None:
    value = (
        await db.execute(
            text("SELECT encode(sha256, 'hex') FROM platform.files WHERE id = :f"),
            {"f": record.file_id},
        )
    ).scalar_one_or_none()
    return None if value is None else str(value)


async def cancel(
    db: AsyncSession, auth: Authorized, import_id: uuid.UUID, *, row_version: int
) -> ImportRecord:
    record = await get_import(db, auth, import_id)
    if record.status not in ("pending", "validated", "failed"):
        raise ConflictError("This import can't be cancelled now.", code="import_not_cancellable")
    row = (
        await db.execute(
            text(
                "UPDATE platform.imports SET status = 'cancelled' "  # noqa: S608
                f"WHERE id = :id AND row_version = :v RETURNING {_COLUMNS}"
            ),
            {"id": import_id, "v": row_version},
        )
    ).one_or_none()
    if row is None:
        raise ConflictError("This import changed. Reload and try again.", code="stale_row_version")
    await audit.record(db, "import.cancelled", "platform.imports", import_id)
    return ImportRecord(*row)


async def error_file_url(
    db: AsyncSession, settings: Settings, auth: Authorized, import_id: uuid.UUID
) -> str:
    record = await get_import(db, auth, import_id)
    if record.error_file_id is None:
        raise NotFoundError("This import has no error file.")
    file = await files.get_file(db, record.error_file_id)
    if file is None or file.purged_at is not None:
        raise NotFoundError("The error file has been removed.")
    url = await storage.presign_download(
        settings,
        file.bucket,
        file.storage_key,
        filename=file.original_name,
        content_type="text/csv",
        expires_in=settings.files_download_ttl_seconds,
    )
    await audit.record(db, "file.download", "platform.files", file.id)
    return url
