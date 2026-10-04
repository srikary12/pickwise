# SPDX-License-Identifier: AGPL-3.0-only
"""The file pipeline end to end against the real object store (SeaweedFS in the test stack)."""

import io
import uuid
import zipfile
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from pickwise.platform import storage
from pickwise.platform.files import processing
from pickwise.platform.files.access import FILE_ACCESS
from pickwise.platform.scanning import ScannerError, eicar_bytes
from pickwise.shared.db import ops_task
from pickwise.shared.settings import Settings
from tests.integration.conftest import MakeApi
from tests.integration.support import Account, Api, Env, Tenant

pytestmark = pytest.mark.db

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def office_zip(folder: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(f"{folder}/document.xml", "<x/>")
    return buffer.getvalue()


async def request_upload(
    api: Api, name: str = "letter.pdf", mime: str = PDF_TYPE, size: int = 100, **extra: Any
) -> httpx.Response:
    return await api.post(
        "/v1/files", {"filename": name, "mime_type": mime, "size_bytes": size, **extra}
    )


async def put_object(slot: dict[str, Any], content: bytes, name: str = "letter.pdf") -> int:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            slot["upload_url"], data=slot["upload_fields"], files={"file": (name, content)}
        )
    return response.status_code


async def upload(
    api: Api,
    env: Env,
    content: bytes,
    name: str = "letter.pdf",
    mime: str = PDF_TYPE,
    **extra: Any,
) -> dict[str, Any]:
    """Slot → object → complete. Returns the file as the API describes it."""
    created = await request_upload(api, name, mime, len(content), **extra)
    assert created.status_code == 201, created.text
    slot = created.json()
    assert await put_object(slot, content, name) == 204
    done = await api.post(f"/v1/files/{slot['file']['id']}/complete")
    assert done.status_code == 202, done.text
    body: dict[str, Any] = done.json()
    return body


async def fetch(url: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(url)


async def object_exists(settings: Settings, bucket: str, key: str) -> bool:
    async with storage.s3_client(settings) as s3:
        return await storage.head_object(s3, bucket, key) is not None


async def tenant_file_key(env: Env, file_id: str) -> str:
    ((key,),) = await env.sql("SELECT storage_key FROM platform.files WHERE id = :i", i=file_id)
    return str(key)


@pytest.fixture
async def member(make_api: MakeApi, env: Env, tenant: Tenant) -> tuple[Api, Account]:
    account = await env.add_account(tenant, "employee")
    return await (await make_api()).sign_in(account), account


# --- the happy path -----------------------------------------------------------------------


async def test_upload_scan_and_download(
    member: tuple[Api, Account], env: Env, tenant: Tenant
) -> None:
    api, _ = member
    file = await upload(api, env, PDF)
    assert file["scan_status"] == "pending"
    assert file["size_bytes"] == len(PDF)
    assert any(j[0] == "pickwise.scan_file" and j[1]["file_id"] == file["id"] for j in env.jobs)

    # Not downloadable until scanned.
    early = await api.get(f"/v1/files/{file['id']}/download")
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "file_not_ready"

    outcome = await env.scan_file(tenant.id, uuid.UUID(file["id"]))
    assert outcome.status == "clean"
    status = (await api.get(f"/v1/files/{file['id']}")).json()
    assert status["scan_status"] == "clean"
    assert status["mime_type"] == PDF_TYPE

    link = (await api.get(f"/v1/files/{file['id']}/download")).json()
    served = await fetch(link["url"])
    assert served.status_code == 200
    assert served.content == PDF
    assert served.headers["content-type"] == PDF_TYPE
    assert served.headers["content-disposition"].startswith("attachment")

    # The bytes moved: clean bucket has them, quarantine doesn't.
    key = await tenant_file_key(env, file["id"])
    assert await object_exists(env.settings, env.settings.s3_bucket_files, key)
    assert not await object_exists(env.settings, env.settings.s3_bucket_quarantine, key)
    ((sha,),) = await env.sql("SELECT sha256 FROM platform.files WHERE id = :i", i=file["id"])
    import hashlib

    assert bytes(sha) == hashlib.sha256(PDF).digest()


async def test_office_and_text_files_are_recognised(
    member: tuple[Api, Account], env: Env, tenant: Tenant
) -> None:
    api, _ = member
    cases = [
        ("cv.docx", DOCX_TYPE, office_zip("word")),
        ("people.csv", "text/csv", b"name,email\nAsha,asha@example.test\n"),
        ("notes.txt", "text/plain", "héllo, wörld\n".encode()),
        ("photo.png", "image/png", PNG),
    ]
    for name, mime, content in cases:
        file = await upload(api, env, content, name, mime)
        outcome = await env.scan_file(tenant.id, uuid.UUID(file["id"]))
        assert outcome.status == "clean", name


# --- rejection ------------------------------------------------------------------------------


async def test_eicar_is_rejected_end_to_end(
    member: tuple[Api, Account], env: Env, tenant: Tenant
) -> None:
    api, _ = member
    file = await upload(api, env, eicar_bytes(), "virus.txt", "text/plain")
    outcome = await env.scan_file(tenant.id, uuid.UUID(file["id"]))
    assert outcome.status == "infected"

    status = (await api.get(f"/v1/files/{file['id']}")).json()
    assert status["scan_status"] == "infected"
    assert "Eicar" in status["scan_detail"]
    refused = await api.get(f"/v1/files/{file['id']}/download")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "file_unavailable"
    key = await tenant_file_key(env, file["id"])
    assert not await object_exists(env.settings, env.settings.s3_bucket_quarantine, key)
    assert not await object_exists(env.settings, env.settings.s3_bucket_files, key)
    ((count,),) = await env.sql(
        "SELECT count(*) FROM audit.events WHERE tenant_id = :t AND action = 'file.infected' "
        "AND entity_id = :e",
        t=tenant.id,
        e=uuid.UUID(file["id"]),
    )
    assert count == 1


async def test_content_that_isnt_what_it_claims_is_rejected(
    member: tuple[Api, Account], env: Env, tenant: Tenant
) -> None:
    api, _ = member
    for name, mime, content in [
        ("fake.pdf", PDF_TYPE, PNG),
        ("fake.docx", DOCX_TYPE, office_zip("xl")),  # a spreadsheet renamed as a document
        ("fake.png", "image/png", b"\x00\x01\x02 not an image"),
    ]:
        file = await upload(api, env, content, name, mime)
        outcome = await env.scan_file(tenant.id, uuid.UUID(file["id"]))
        assert outcome.status == "error", name
        status = (await api.get(f"/v1/files/{file['id']}")).json()
        assert status["scan_status"] == "error"
        assert "type" in status["scan_detail"]
        refused = await api.get(f"/v1/files/{file['id']}/download")
        assert refused.json()["error"]["code"] == "file_unavailable"


@pytest.mark.parametrize(
    ("name", "mime", "size", "code"),
    [
        ("tool.exe", "application/x-msdownload", 100, "file_type_not_allowed"),
        ("letter.exe", PDF_TYPE, 100, "file_extension_mismatch"),
        ("big.pdf", PDF_TYPE, 26 * 1024 * 1024, "file_too_large"),
    ],
)
async def test_unacceptable_requests_are_refused_up_front(
    member: tuple[Api, Account], name: str, mime: str, size: int, code: str
) -> None:
    response = await request_upload(member[0], name, mime, size)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


async def test_the_store_enforces_the_size_cap(
    member: tuple[Api, Account],
    app: FastAPI,
    api_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app.state, "settings", api_settings.model_copy(update={"files_max_bytes": 100})
    )
    api, _ = member
    slot = (await request_upload(api, size=50)).json()
    assert await put_object(slot, PDF + b"x" * 200) == 400  # EntityTooLarge


async def test_scanner_outage_leaves_the_file_pending(
    member: tuple[Api, Account], env: Env, tenant: Tenant
) -> None:
    class Down:
        name = "down"

        async def scan(self, chunks: AsyncIterator[bytes]) -> Any:
            raise ScannerError("clamd unreachable")

        async def ping(self) -> bool:
            return False

    api, _ = member
    file = await upload(api, env, PDF)
    with pytest.raises(ScannerError):
        await env.scan_file(tenant.id, uuid.UUID(file["id"]), Down())
    assert (await api.get(f"/v1/files/{file['id']}")).json()["scan_status"] == "pending"
    # A later attempt succeeds.
    assert (await env.scan_file(tenant.id, uuid.UUID(file["id"]))).status == "clean"


# --- completion ----------------------------------------------------------------------------


async def test_completing_before_uploading_is_a_conflict(member: tuple[Api, Account]) -> None:
    api, _ = member
    slot = (await request_upload(api)).json()
    response = await api.post(f"/v1/files/{slot['file']['id']}/complete")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "upload_missing"


async def test_completing_twice_queues_one_scan(member: tuple[Api, Account], env: Env) -> None:
    api, _ = member
    file = await upload(api, env, PDF)
    again = await api.post(f"/v1/files/{file['id']}/complete")
    assert again.status_code == 202
    queued = [j for j in env.jobs if j[1].get("file_id") == file["id"]]
    assert len(queued) == 1


async def test_a_scan_is_idempotent(member: tuple[Api, Account], env: Env, tenant: Tenant) -> None:
    api, _ = member
    file = await upload(api, env, PDF)
    assert (await env.scan_file(tenant.id, uuid.UUID(file["id"]))).status == "clean"
    assert (await env.scan_file(tenant.id, uuid.UUID(file["id"]))).status == "skipped"


async def test_unfinished_uploads_expire(member: tuple[Api, Account], env: Env) -> None:
    api, _ = member
    slot = (await request_upload(api)).json()
    file_id = slot["file"]["id"]
    await put_object(slot, PDF)
    await env.sql(
        "UPDATE platform.files SET upload_expires_at = now() - interval '1 minute' WHERE id = :i",
        i=file_id,
    )

    @ops_task
    async def expire() -> int:
        async with storage.s3_client(env.settings) as s3, env.db.ops_session() as s:
            return await processing.expire_unfinished(s, env.settings, s3)

    assert await expire() >= 1
    closed = await api.post(f"/v1/files/{file_id}/complete")
    assert closed.status_code == 409
    assert closed.json()["error"]["code"] == "upload_expired"
    key = await tenant_file_key(env, file_id)
    assert not await object_exists(env.settings, env.settings.s3_bucket_quarantine, key)


async def test_sweep_requeues_and_fails_stuck_scans(
    member: tuple[Api, Account], env: Env, tenant: Tenant
) -> None:
    api, _ = member
    queued = await upload(api, env, PDF)
    stuck = await upload(api, env, PDF)
    # uploaded_at isn't immutable: age the rows as the sweep sees them.
    await env.sql(
        "UPDATE platform.files SET uploaded_at = now() - interval '5 minutes' WHERE id = :i",
        i=queued["id"],
    )
    await env.sql(
        "UPDATE platform.files SET uploaded_at = now() - interval '2 hours' WHERE id = :i",
        i=stuck["id"],
    )

    @ops_task
    async def sweep() -> tuple[list[tuple[uuid.UUID, uuid.UUID]], int]:
        async with env.db.ops_session() as s:
            return await processing.sweep(s)

    pending, failed = await sweep()
    assert (tenant.id, uuid.UUID(queued["id"])) in pending
    assert failed >= 1
    assert (await api.get(f"/v1/files/{stuck['id']}")).json()["scan_status"] == "error"


async def test_retention_removes_the_bytes_but_keeps_the_record(
    member: tuple[Api, Account], env: Env, tenant: Tenant
) -> None:
    api, _ = member
    file = await upload(api, env, PDF)
    await env.scan_file(tenant.id, uuid.UUID(file["id"]))
    key = await tenant_file_key(env, file["id"])
    await env.sql(
        "UPDATE platform.files SET retention_until = now() - interval '1 day' WHERE id = :i",
        i=file["id"],
    )

    @ops_task
    async def purge() -> int:
        async with storage.s3_client(env.settings) as s3, env.db.ops_session() as s:
            return await processing.purge_retained(s, s3)

    assert await purge() >= 1
    assert not await object_exists(env.settings, env.settings.s3_bucket_files, key)
    gone = await api.get(f"/v1/files/{file['id']}/download")
    assert gone.status_code == 410
    assert (await api.get(f"/v1/files/{file['id']}")).status_code == 200


# --- who may read ---------------------------------------------------------------------------


@pytest.fixture
def owner_rule() -> Iterator[list[uuid.UUID]]:
    """A module-style rule: members listed here may read files owned by 'widget' entities."""
    allowed: list[uuid.UUID] = []

    async def rule(_db: Any, principal: Any, file: Any) -> bool:
        return principal.user_id in allowed

    FILE_ACCESS.register("widget", rule)
    yield allowed
    FILE_ACCESS.unregister("widget")


async def test_only_permitted_callers_can_read_a_file(
    make_api: MakeApi, env: Env, tenant: Tenant, owner_rule: list[uuid.UUID]
) -> None:
    uploader = await env.add_account(tenant, "employee")
    colleague = await env.add_account(tenant, "employee")
    outsider = await env.add_account(tenant, "employee")
    hr = await env.add_account(tenant, "hr_admin")
    stranger_tenant = await env.create_tenant()
    api = await (await make_api()).sign_in(uploader)
    file = await upload(
        api, env, PDF, owner_entity_type="widget", owner_entity_id=str(uuid.uuid4())
    )
    await env.scan_file(tenant.id, uuid.UUID(file["id"]))
    owner_rule.append(colleague.user_id)

    async def status_for(account: Account) -> int:
        client = await (await make_api()).sign_in(account)
        return (await client.get(f"/v1/files/{file['id']}/download")).status_code

    assert (await api.get(f"/v1/files/{file['id']}/download")).status_code == 200
    assert await status_for(hr) == 200  # platform.files.read_all
    assert await status_for(colleague) == 200  # the owning module's rule
    assert await status_for(outsider) == 404  # not found, not forbidden: ids can't be probed
    assert await status_for(stranger_tenant.admin) == 404  # another tenant: RLS


async def test_create_upload_replays_without_the_credential(
    member: tuple[Api, Account],
) -> None:
    api, _ = member
    headers = {"Idempotency-Key": f"up-{uuid.uuid4().hex}"}
    body = {"filename": "a.pdf", "mime_type": PDF_TYPE, "size_bytes": 10}
    first = await api.post("/v1/files", body, headers=headers)
    second = await api.post("/v1/files", body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["file"]["id"] == second.json()["file"]["id"]
    assert first.json()["upload_fields"]
    assert second.json()["upload_fields"] == {}


async def test_uploads_need_the_permission(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    key = (
        await admin.post(
            "/v1/admin/api-keys", {"name": "reader", "scopes": ["platform.files.read"]}
        )
    ).json()["key"]
    client = await make_api()
    refused = await client.post(
        "/v1/files",
        {"filename": "a.pdf", "mime_type": PDF_TYPE, "size_bytes": 10},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert refused.status_code == 403
