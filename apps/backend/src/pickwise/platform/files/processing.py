# SPDX-License-Identifier: AGPL-3.0-only
"""The scan pipeline: quarantine → scan → sniff → clean bucket (ADR 0013). Runs in ops sessions.

Order matters for crash safety: the clean copy is written first, the row is updated and
committed, and only then is the quarantine object deleted. A crash in between leaves the
row ``pending`` and the work is simply repeated.
"""

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import IO, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit, storage
from pickwise.platform.files import types
from pickwise.platform.scanning import Scanner
from pickwise.shared.logging import get_logger
from pickwise.shared.settings import Settings

log = get_logger(__name__)

SPOOL_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str  # clean / infected / error / skipped
    # A quarantine object to delete once the transaction has committed.
    cleanup: tuple[str, str] | None = None


async def _chunks(stream: IO[bytes], size: int = 64 * 1024) -> AsyncIterator[bytes]:
    stream.seek(0)
    while chunk := stream.read(size):
        yield chunk


def _sha256(stream: IO[bytes]) -> bytes:
    stream.seek(0)
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.digest()


async def _set_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
    )


async def _reject(
    session: AsyncSession, file_id: uuid.UUID, status: str, detail: str, action: str
) -> None:
    await session.execute(
        text(
            "UPDATE platform.files SET scan_status = :s, scan_detail = :d, scanned_at = now() "
            "WHERE id = :id"
        ),
        {"s": status, "d": detail[:500], "id": file_id},
    )
    await audit.record(session, action, "platform.files", file_id, {"detail": detail[:200]})


async def scan_file(
    session: AsyncSession,
    settings: Settings,
    scanner: Scanner,
    s3: Any,
    tenant_id: uuid.UUID,
    file_id: uuid.UUID,
) -> Outcome:
    """Scan one uploaded file. Raises ScannerError when no verdict was possible (retry)."""
    await _set_tenant(session, tenant_id)
    row = (
        await session.execute(
            text(
                "SELECT storage_key, bucket, declared_mime_type, original_name, scan_status, "
                "       uploaded_at FROM platform.files WHERE id = :id FOR UPDATE"
            ),
            {"id": file_id},
        )
    ).one_or_none()
    if row is None or row.scan_status != "pending" or row.uploaded_at is None:
        return Outcome("skipped")
    quarantine = (settings.s3_bucket_quarantine, row.storage_key)

    head = await storage.head_object(s3, *quarantine)
    if head is None:
        await _reject(session, file_id, "error", "uploaded object is missing", "file.rejected")
        return Outcome("error")
    if int(head["ContentLength"]) > settings.files_max_bytes:
        await _reject(session, file_id, "error", "file too large", "file.rejected")
        return Outcome("error", quarantine)

    with SpooledTemporaryFile(max_size=SPOOL_BYTES) as tmp:
        await storage.download_to(s3, *quarantine, tmp)
        size = tmp.tell()
        result = await scanner.scan(_chunks(tmp))
        if result.infected:
            await _reject(
                session, file_id, "infected", result.signature or "infected", "file.infected"
            )
            log.warning("file infected", file_id=str(file_id), tenant_id=str(tenant_id))
            return Outcome("infected", quarantine)

        sniffed = types.sniff_mime(tmp)
        problem = None
        if sniffed is None or sniffed not in settings.files_allowed_mime_types:
            problem = f"type not allowed (detected {sniffed or 'unknown'})"
        elif not types.compatible(row.declared_mime_type, sniffed):
            problem = f"type mismatch (declared {row.declared_mime_type}, detected {sniffed})"
        if problem:
            await _reject(session, file_id, "error", problem, "file.rejected")
            return Outcome("error", quarantine)
        digest = _sha256(tmp)

    await s3.copy_object(
        Bucket=settings.s3_bucket_files,
        Key=row.storage_key,
        CopySource={"Bucket": quarantine[0], "Key": quarantine[1]},
        ContentType=sniffed,
        MetadataDirective="REPLACE",
    )
    await session.execute(
        text(
            "UPDATE platform.files SET scan_status = 'clean', scan_detail = NULL, "
            "scanned_at = now(), bucket = :bucket, mime_type = :mime, size_bytes = :size, "
            "sha256 = :sha WHERE id = :id"
        ),
        {
            "bucket": settings.s3_bucket_files,
            "mime": sniffed,
            "size": size,
            "sha": digest,
            "id": file_id,
        },
    )
    await audit.record(session, "file.clean", "platform.files", file_id)
    return Outcome("clean", quarantine)


async def sweep(session: AsyncSession) -> tuple[list[tuple[uuid.UUID, uuid.UUID]], int]:
    """Ops session. Returns (uploaded files still pending for over a minute, stuck files failed).

    A scan that can't get a verdict for an hour is failed rather than left pending forever.
    """
    stuck = (
        await session.execute(
            text(
                "UPDATE platform.files SET scan_status = 'error', "
                "  scan_detail = 'no scan verdict within an hour', scanned_at = now() "
                "WHERE scan_status = 'pending' AND uploaded_at < now() - interval '1 hour' "
                "RETURNING id"
            )
        )
    ).all()
    pending = (
        await session.execute(
            text(
                "SELECT tenant_id, id FROM platform.files WHERE scan_status = 'pending' "
                "AND uploaded_at IS NOT NULL AND uploaded_at < now() - interval '1 minute' "
                "ORDER BY uploaded_at LIMIT 100"
            )
        )
    ).all()
    return [(r[0], r[1]) for r in pending], len(stuck)


async def expire_unfinished(session: AsyncSession, settings: Settings, s3: Any) -> int:
    """Ops session. Upload slots nobody completed: drop any partial object, close the slot."""
    rows = (
        await session.execute(
            text(
                "UPDATE platform.files SET scan_status = 'error', scan_detail = 'upload expired', "
                "  purged_at = now() "
                "WHERE scan_status = 'pending' AND uploaded_at IS NULL "
                "  AND upload_expires_at < now() RETURNING storage_key"
            )
        )
    ).all()
    for (key,) in rows:
        await storage.delete_object(s3, settings.s3_bucket_quarantine, key)
    return len(rows)


async def purge_retained(session: AsyncSession, s3: Any) -> int:
    """Ops session. Delete the bytes of files past ``retention_until`` (the row stays)."""
    rows = (
        await session.execute(
            text(
                "UPDATE platform.files SET purged_at = now() "
                "WHERE retention_until < now() AND purged_at IS NULL "
                "RETURNING bucket, storage_key"
            )
        )
    ).all()
    for bucket, key in rows:
        await storage.delete_object(s3, bucket, key)
    return len(rows)
