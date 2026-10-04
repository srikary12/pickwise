# SPDX-License-Identifier: AGPL-3.0-only
"""File endpoints (/v1/files): presigned upload, completion, status, download."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status

from pickwise.platform import jobs
from pickwise.platform.auth.dependencies import DB, Authorized, SettingsDep, require
from pickwise.platform.files import service
from pickwise.platform.files.schemas import DownloadOut, FileOut, UploadRequest, UploadSlotOut
from pickwise.platform.idempotency import idempotent
from pickwise.shared.errors import NotFoundError

router = APIRouter(prefix="/v1/files", tags=["files"])

Upload = Annotated[Authorized, Depends(require("platform.files.upload"))]
Read = Annotated[Authorized, Depends(require("platform.files.read"))]


def _out(file: service.FileRecord) -> FileOut:
    return FileOut(
        id=file.id,
        original_name=file.original_name,
        mime_type=file.mime_type,
        size_bytes=file.size_bytes,
        scan_status=file.scan_status,
        scan_detail=file.scan_detail,
        classification=file.classification,
        owner_entity_type=file.owner_entity_type,
        owner_entity_id=file.owner_entity_id,
        uploaded_at=file.uploaded_at,
        scanned_at=file.scanned_at,
    )


@router.post("", response_model=UploadSlotOut, status_code=status.HTTP_201_CREATED)
async def create_upload(
    body: UploadRequest,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    auth: Upload,
) -> Any:
    """Ask for an upload slot. POST the file to ``upload_url`` (quarantine), then call
    ``/complete``. The file can't be downloaded until it has been scanned clean."""
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    slot = await service.create_upload(db, settings, auth, **body.model_dump())
    out = UploadSlotOut(
        file=_out(slot.file),
        upload_url=slot.url,
        upload_fields=slot.fields,
        expires_at=slot.file.upload_expires_at,
    )
    # The presigned form is a credential: a replay returns the slot without it.
    await idem.finish(status.HTTP_201_CREATED, {**out.model_dump(mode="json"), "upload_fields": {}})
    return out


@router.post("/{file_id}/complete", response_model=FileOut, status_code=status.HTTP_202_ACCEPTED)
async def complete_upload(
    file_id: uuid.UUID,
    background: BackgroundTasks,
    db: DB,
    settings: SettingsDep,
    auth: Upload,
) -> FileOut:
    """Tell us the upload finished; the scan is queued from here (no bucket notifications)."""
    file, scan = await service.complete_upload(db, settings, auth, file_id)
    if scan:
        # Runs after the response, i.e. after this request's commit.
        background.add_task(
            jobs.defer,
            "pickwise.scan_file",
            file_id=str(file.id),
            tenant_id=str(auth.tenant_id),
        )
    return _out(file)


@router.get("/{file_id}", response_model=FileOut)
async def get_file(file_id: uuid.UUID, db: DB, auth: Read) -> FileOut:
    file = await service.get_file(db, file_id)
    if file is None:
        raise NotFoundError("File not found.")
    await service.authorize_read(db, auth, file)
    return _out(file)


@router.get("/{file_id}/download", response_model=DownloadOut)
async def download(file_id: uuid.UUID, db: DB, settings: SettingsDep, auth: Read) -> DownloadOut:
    """A short-lived presigned URL: only for clean files the caller may read."""
    url, _ = await service.download_url(db, settings, auth, file_id)
    return DownloadOut(url=url, expires_in=settings.files_download_ttl_seconds)
