# SPDX-License-Identifier: AGPL-3.0-only
"""Worker side of imports: the dry run and the batched commit (ADR 0019).

Both read the uploaded file from the clean bucket and check its SHA-256 against the
hash the files pipeline recorded, so the bytes that are committed are the bytes that
were validated. Validation and writing run in tenant sessions (RLS applies to module
code); bookkeeping (status, progress, the error file) runs in ops sessions so it
survives a failing batch.
"""

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import IO, Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit, storage
from pickwise.platform.imports.parsers import ImportFileError, Parsed, parse
from pickwise.platform.imports.registry import IMPORT_TYPES, ImportRow, ImportType, RowError
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.csvsafe import safe_cell
from pickwise.shared.db import Database, ops_task
from pickwise.shared.ids import uuid7
from pickwise.shared.logging import get_logger
from pickwise.shared.settings import Settings

log = get_logger(__name__)

SPOOL_BYTES = 8 * 1024 * 1024
STALE_AFTER_MINUTES = 30


@dataclass(frozen=True, slots=True)
class _Job:
    import_type: str
    file_id: uuid.UUID
    created_by: uuid.UUID | None
    bucket: str
    storage_key: str
    mime_type: str
    sha256_hex: str
    stats: dict[str, Any]


def error_csv(errors: list[tuple[int, str | None, str]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["row", "column", "message"])
    for line, column, message in errors:
        writer.writerow([line, safe_cell(column or ""), safe_cell(message)])
    return out.getvalue().encode("utf-8")


async def _patch(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    import_id: uuid.UUID,
    *,
    status: str | None = None,
    stats: dict[str, Any] | None = None,
    error_file_id: uuid.UUID | None = None,
) -> None:
    await session.execute(
        text(
            "UPDATE platform.imports SET status = coalesce(:status, status), "
            "  stats = stats || CAST(:stats AS jsonb), "
            "  error_file_id = coalesce(:err, error_file_id) "
            "WHERE tenant_id = :t AND id = :id"
        ).bindparams(bindparam("stats", type_=JSONB)),
        {
            "status": status,
            "stats": stats or {},
            "err": error_file_id,
            "t": tenant_id,
            "id": import_id,
        },
    )


async def _claim(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    import_id: uuid.UUID,
    *,
    expect: str,
    move_to: str | None,
) -> _Job | None:
    row = (
        await session.execute(
            text(
                "SELECT i.import_type, i.file_id, i.created_by, i.stats, f.bucket, f.storage_key, "
                "  f.mime_type, encode(f.sha256, 'hex') AS sha, f.scan_status, f.purged_at "
                "FROM platform.imports i JOIN platform.files f "
                "  ON f.tenant_id = i.tenant_id AND f.id = i.file_id "
                "WHERE i.tenant_id = :t AND i.id = :id AND i.status = :expect "
                "FOR UPDATE OF i SKIP LOCKED"
            ),
            {"t": tenant_id, "id": import_id, "expect": expect},
        )
    ).one_or_none()
    if row is None:
        return None
    if move_to:
        await _patch(session, tenant_id, import_id, status=move_to)
    if row.scan_status != "clean" or row.purged_at is not None or row.sha is None:
        await _patch(
            session,
            tenant_id,
            import_id,
            status="failed",
            stats={
                "error": "file_unavailable",
                "error_message": "The file is no longer available.",
            },
        )
        return None
    return _Job(
        row.import_type,
        row.file_id,
        row.created_by,
        row.bucket,
        row.storage_key,
        row.mime_type,
        row.sha,
        row.stats,
    )


@ops_task
async def _fail(
    database: Database,
    tenant_id: uuid.UUID,
    import_id: uuid.UUID,
    code: str,
    message: str,
    phase: str,
    extra: dict[str, Any] | None = None,
) -> str:
    async with database.ops_session() as session:
        await _patch(
            session,
            tenant_id,
            import_id,
            status="failed",
            stats={"error": code, "error_message": message, "phase": phase, **(extra or {})},
        )
    log.warning("import failed", import_id=str(import_id), code=code, phase=phase)
    return "failed"


async def _download(s3: Any, job: _Job) -> tuple[IO[bytes], str]:
    buffer = SpooledTemporaryFile(max_size=SPOOL_BYTES)  # noqa: SIM115 - closed by the caller
    await storage.download_to(s3, job.bucket, job.storage_key, buffer)
    buffer.seek(0)
    digest = hashlib.sha256()
    while chunk := buffer.read(1024 * 1024):
        digest.update(chunk)
    buffer.seek(0)
    return buffer, digest.hexdigest()


def _batches(parsed: Parsed, size: int) -> Any:
    batch: list[ImportRow] = []
    for row in parsed.rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


async def _check_row(
    session: AsyncSession, import_type: ImportType, row: ImportRow
) -> list[RowError]:
    errors = [
        RowError("is required", c.name)
        for c in import_type.columns
        if c.required and not row.values.get(c.name)
    ]
    errors.extend(await import_type.validate_row(session, row))
    return errors


def _context(tenant_id: uuid.UUID, import_id: uuid.UUID) -> RequestContext:
    return RequestContext(ActorType.WORKER, tenant_id, request_id=f"import:{import_id}")


@ops_task
async def run_validation(
    database: Database, settings: Settings, s3: Any, tenant_id: uuid.UUID, import_id: uuid.UUID
) -> str:
    """The dry run: parse, validate every row, write an error file if there are problems."""
    async with database.ops_session() as session:
        job = await _claim(session, tenant_id, import_id, expect="pending", move_to="validating")
    if job is None:
        return "skipped"
    import_type = IMPORT_TYPES.get(job.import_type)
    if import_type is None:
        return await _fail(
            database,
            tenant_id,
            import_id,
            "unknown_import_type",
            "This kind of import is no longer available.",
            "validate",
        )
    buffer, digest = await _download(s3, job)
    try:
        if digest != job.sha256_hex:
            return await _fail(
                database,
                tenant_id,
                import_id,
                "file_changed",
                "The stored file doesn't match what was uploaded.",
                "validate",
            )
        errors: list[tuple[int, str | None, str]] = []
        error_lines: set[int] = set()
        total = 0
        error_total = 0
        try:
            parsed = parse(
                buffer, job.mime_type, import_type.columns, max_rows=settings.imports_max_rows
            )
            for batch in _batches(parsed, settings.imports_batch_size):
                async with database.tenant_session(_context(tenant_id, import_id)) as session:
                    for row in batch:
                        total += 1
                        found = await _check_row(session, import_type, row)
                        if found:
                            error_lines.add(row.line)
                        for err in found:
                            error_total += 1
                            if len(errors) < settings.imports_max_errors:
                                errors.append((row.line, err.column, err.message))
        except ImportFileError as exc:
            return await _fail(database, tenant_id, import_id, "invalid_file", str(exc), "validate")
        error_file_id: uuid.UUID | None = None
        if errors:
            error_file_id = await _store_error_file(
                database, settings, s3, tenant_id, import_id, job.created_by, errors
            )
        async with database.ops_session() as session:
            await _patch(
                session,
                tenant_id,
                import_id,
                status="validated",
                stats={
                    "phase": "validate",
                    "file_sha256": digest,
                    "rows": total,
                    "error_rows": len(error_lines),
                    "valid_rows": total - len(error_lines),
                    "errors_total": error_total,
                    "errors_truncated": error_total > len(errors),
                    "error": None,
                    "error_message": None,
                },
                error_file_id=error_file_id,
            )
        return "validated"
    finally:
        buffer.close()


@ops_task
async def _store_error_file(
    database: Database,
    settings: Settings,
    s3: Any,
    tenant_id: uuid.UUID,
    import_id: uuid.UUID,
    created_by: uuid.UUID | None,
    errors: list[tuple[int, str | None, str]],
) -> uuid.UUID:
    content = error_csv(errors)
    file_id = uuid7()
    key = f"{tenant_id}/{file_id}"
    await s3.put_object(
        Bucket=settings.s3_bucket_files, Key=key, Body=content, ContentType="text/csv"
    )
    async with database.ops_session() as session:
        await session.execute(
            text(
                "INSERT INTO platform.files (tenant_id, id, storage_key, bucket, original_name, "
                "  declared_mime_type, declared_size_bytes, mime_type, size_bytes, sha256, "
                "  scan_status, scanned_at, uploaded_at, classification, owner_entity_type, "
                "  owner_entity_id, created_by) "
                "VALUES (:t, :id, :key, :bucket, :name, 'text/csv', :size, 'text/csv', :size, "
                "  :sha, 'clean', now(), now(), 'confidential', 'import', :owner, :by)"
            ),
            {
                "t": tenant_id,
                "id": file_id,
                "key": key,
                "bucket": settings.s3_bucket_files,
                "name": f"import-errors-{import_id}.csv",
                "size": len(content),
                "sha": hashlib.sha256(content).digest(),
                "owner": import_id,
                "by": created_by,
            },
        )
    return file_id


@ops_task
async def run_commit(
    database: Database, settings: Settings, s3: Any, tenant_id: uuid.UUID, import_id: uuid.UUID
) -> str:
    """Write the rows in batches, re-checking each batch just before it is written."""
    async with database.ops_session() as session:
        job = await _claim(session, tenant_id, import_id, expect="committing", move_to=None)
    if job is None:
        return "skipped"
    import_type = IMPORT_TYPES.get(job.import_type)
    if import_type is None:
        return await _fail(
            database,
            tenant_id,
            import_id,
            "unknown_import_type",
            "This kind of import is no longer available.",
            "commit",
        )
    buffer, digest = await _download(s3, job)
    try:
        if digest != job.stats.get("file_sha256") or digest != job.sha256_hex:
            return await _fail(
                database,
                tenant_id,
                import_id,
                "file_changed",
                "The file changed since the dry run. Import it again.",
                "commit",
            )
        committed = 0
        try:
            parsed = parse(
                buffer, job.mime_type, import_type.columns, max_rows=settings.imports_max_rows
            )
            for batch in _batches(parsed, settings.imports_batch_size):
                async with database.tenant_session(_context(tenant_id, import_id)) as session:
                    bad = [r.line for r in batch if await _check_row(session, import_type, r)]
                    if bad:
                        return await _fail(
                            database,
                            tenant_id,
                            import_id,
                            "revalidation_failed",
                            "Data changed since the dry run and some rows no longer validate. "
                            "Run the dry run again.",
                            "commit",
                            {"committed_rows": committed, "first_bad_row": bad[0]},
                        )
                    await import_type.commit_rows(session, tenant_id, batch)
                committed += len(batch)
                async with database.ops_session() as session:
                    await _patch(session, tenant_id, import_id, stats={"committed_rows": committed})
        except ImportFileError as exc:
            return await _fail(database, tenant_id, import_id, "invalid_file", str(exc), "commit")
        except Exception as exc:  # noqa: BLE001 - the module's committer; type only is logged
            log.error("import commit failed", import_id=str(import_id), error=type(exc).__name__)
            return await _fail(
                database,
                tenant_id,
                import_id,
                "commit_failed",
                "Writing the rows failed. Nothing is lost: committing again continues safely.",
                "commit",
                {"committed_rows": committed},
            )
        async with database.ops_session() as session:
            await _patch(
                session,
                tenant_id,
                import_id,
                status="committed",
                stats={"committed_rows": committed, "error": None, "error_message": None},
            )
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
            )
            await audit.record(
                session,
                "import.committed",
                "platform.imports",
                import_id,
                {"import_type": job.import_type, "rows": committed},
            )
        return "committed"
    finally:
        buffer.close()


async def sweep(
    session: AsyncSession,
    *,
    pending_after_seconds: int = 120,
    stale_after_minutes: int = STALE_AFTER_MINUTES,
) -> tuple[list[tuple[uuid.UUID, uuid.UUID]], int]:
    """Ops session. Imports still ``pending`` after two minutes (their job was lost) to
    re-queue, and ones stuck mid-run for too long to fail (a failed commit can be retried)."""
    pending = (
        await session.execute(
            text(
                "SELECT tenant_id, id FROM platform.imports "
                "WHERE status = 'pending' AND created_at < now() - make_interval(secs => :s) "
                "LIMIT 100"
            ),
            {"s": pending_after_seconds},
        )
    ).all()
    stuck = (
        await session.execute(
            text(
                "UPDATE platform.imports SET status = 'failed', "
                "  stats = stats || jsonb_build_object('error', 'timed_out', 'error_message', "
                "    'The import stopped responding.', 'phase', "
                "    CASE WHEN status = 'committing' THEN 'commit' ELSE 'validate' END) "
                "WHERE status IN ('validating', 'committing') "
                "  AND updated_at < now() - make_interval(mins => :m) RETURNING id"
            ),
            {"m": stale_after_minutes},
        )
    ).all()
    return [(r.tenant_id, r.id) for r in pending], len(stuck)
