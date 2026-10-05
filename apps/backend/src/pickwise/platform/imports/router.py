# SPDX-License-Identifier: AGPL-3.0-only
"""Bulk imports (/v1/imports)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status

from pickwise.platform.auth.dependencies import DB, Authorized, SettingsDep, require
from pickwise.platform.idempotency import idempotent
from pickwise.platform.imports import service
from pickwise.platform.imports.registry import IMPORT_TYPES
from pickwise.platform.imports.schemas import (
    ColumnOut,
    ErrorFileOut,
    ImportCreate,
    ImportOut,
    ImportPage,
    ImportTypeOut,
    RowVersion,
)
from pickwise.platform.jobs import JobQueueUnavailableError, defer
from pickwise.shared.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/v1/imports", tags=["imports"])

Run = Annotated[Authorized, Depends(require("platform.imports.run"))]


def _out(r: service.ImportRecord) -> ImportOut:
    return ImportOut(
        id=r.id,
        import_type=r.import_type,
        file_id=r.file_id,
        status=r.status,
        stats=r.stats,
        has_error_file=r.error_file_id is not None,
        created_at=r.created_at,
        updated_at=r.updated_at,
        row_version=r.row_version,
    )


async def _kick(task: str, tenant_id: uuid.UUID, import_id: uuid.UUID) -> None:
    """Queue the worker job right after commit; the sweep re-queues a lost dry run."""
    try:
        await defer(task, import_id=str(import_id), tenant_id=str(tenant_id))
    except JobQueueUnavailableError:
        pass
    except Exception as exc:  # noqa: BLE001 - never fail the request over a queue hiccup
        log.warning("import kick failed", task=task, error=type(exc).__name__)


@router.get("/types", response_model=list[ImportTypeOut])
async def import_types(auth: Run) -> list[ImportTypeOut]:
    """What can be imported, with the columns each file needs."""
    return [
        ImportTypeOut(
            key=t.key,
            label=t.label,
            description=t.description,
            columns=[
                ColumnOut(
                    name=c.name, label=c.label, required=c.required, description=c.description
                )
                for c in t.columns
            ],
            sample=t.sample,
            permitted=t.permission in auth.grants,
        )
        for t in IMPORT_TYPES.all()
    ]


@router.get("", response_model=ImportPage)
async def list_imports(
    db: DB,
    auth: Run,
    before: Annotated[uuid.UUID | None, Query(description="Cursor: next_cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ImportPage:
    records, cursor = await service.list_imports(db, auth, before=before, limit=limit)
    return ImportPage(items=[_out(r) for r in records], next_cursor=cursor)


@router.post("", response_model=ImportOut, status_code=status.HTTP_201_CREATED)
async def create_import(
    body: ImportCreate,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: DB,
    auth: Run,
) -> Any:
    """Start a dry run of an uploaded file. Nothing is written until you commit."""
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    record = await service.create_import(
        db, auth, import_type=body.import_type, file_id=body.file_id
    )
    background.add_task(_kick, "pickwise.validate_import", auth.tenant_id, record.id)
    out = _out(record)
    await idem.finish(status.HTTP_201_CREATED, out.model_dump(mode="json"))
    return out


@router.get("/{import_id}", response_model=ImportOut)
async def get_import(import_id: uuid.UUID, db: DB, auth: Run) -> ImportOut:
    return _out(await service.get_import(db, auth, import_id))


@router.get("/{import_id}/errors", response_model=ErrorFileOut)
async def error_file(
    import_id: uuid.UUID, db: DB, settings: SettingsDep, auth: Run
) -> ErrorFileOut:
    """A link to the CSV listing each problem row (row, column, message)."""
    url = await service.error_file_url(db, settings, auth, import_id)
    return ErrorFileOut(url=url, expires_in=settings.files_download_ttl_seconds)


@router.post("/{import_id}/commit", response_model=ImportOut)
async def commit_import(
    import_id: uuid.UUID, body: RowVersion, background: BackgroundTasks, db: DB, auth: Run
) -> ImportOut:
    """Write the validated rows. Only an error-free dry run of an unchanged file can commit."""
    record = await service.request_commit(db, auth, import_id, row_version=body.row_version)
    background.add_task(_kick, "pickwise.commit_import", auth.tenant_id, record.id)
    return _out(record)


@router.post("/{import_id}/cancel", response_model=ImportOut)
async def cancel_import(import_id: uuid.UUID, body: RowVersion, db: DB, auth: Run) -> ImportOut:
    return _out(await service.cancel(db, auth, import_id, row_version=body.row_version))
