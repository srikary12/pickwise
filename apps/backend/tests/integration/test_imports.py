# SPDX-License-Identifier: AGPL-3.0-only
"""Bulk imports end to end: upload, dry run, error file, batched commit."""

import io
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import openpyxl
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import storage
from pickwise.platform.files.types import XLSX
from pickwise.platform.imports import (
    IMPORT_TYPES,
    ImportColumn,
    ImportType,
    RowError,
    processing,
)
from pickwise.platform.imports.registry import ImportRow
from pickwise.shared.db import ops_task
from tests.integration.conftest import MakeApi
from tests.integration.support import Account, Api, Env, Tenant
from tests.integration.test_files import fetch, upload

pytestmark = pytest.mark.db

CSV = "text/csv"


@dataclass
class Store:
    """What the stub import type 'people_test' wrote, and knobs to make it misbehave."""

    committed: dict[str, str] = field(default_factory=dict)
    batches: list[int] = field(default_factory=list)
    reject_names: set[str] = field(default_factory=set)
    fail_commit: bool = False


@pytest.fixture
def store() -> Iterator[Store]:
    s = Store()

    async def validate(session: AsyncSession, row: ImportRow) -> list[RowError]:
        errors = []
        if "@" not in row.values["email"]:
            errors.append(RowError("isn't an email address", "email"))
        if row.values["name"] in s.reject_names:
            errors.append(RowError('=HYPERLINK("http://evil.test")', "name"))
        return errors

    async def commit(session: AsyncSession, tenant_id: uuid.UUID, rows: list[ImportRow]) -> None:
        if s.fail_commit:
            raise RuntimeError("the database said no: secret detail")
        s.batches.append(len(rows))
        for r in rows:
            s.committed[r.values["email"]] = r.values["name"]

    IMPORT_TYPES.register(
        ImportType(
            "people_test",
            "People",
            (
                ImportColumn("email", "Email", required=True),
                ImportColumn("name", "Name", required=True),
                ImportColumn("start_date", "Start date"),
            ),
            validate,
            commit,
            description="Stub",
        )
    )
    IMPORT_TYPES.register(
        ImportType(
            "restricted_test",
            "Restricted",
            (ImportColumn("email", "Email", required=True),),
            validate,
            commit,
            permission="platform.webhooks.manage",
        )
    )
    yield s
    IMPORT_TYPES.unregister("people_test")
    IMPORT_TYPES.unregister("restricted_test")


@pytest.fixture
async def hr(make_api: MakeApi, env: Env, tenant: Tenant) -> tuple[Api, Account]:
    account = await env.add_account(tenant, "hr_admin")
    return await (await make_api()).sign_in(account), account


def csv_bytes(*lines: str) -> bytes:
    return ("email,name,start_date\n" + "\n".join(lines) + "\n").encode()


async def stage(
    api: Api,
    env: Env,
    tenant: Tenant,
    content: bytes,
    *,
    name: str = "people.csv",
    mime: str = CSV,
    import_type: str = "people_test",
) -> dict[str, Any]:
    """Upload and scan a file, create the import, and run its dry run like the worker."""
    file = await upload(api, env, content, name, mime)
    await env.scan_file(tenant.id, uuid.UUID(file["id"]))
    created = await api.post("/v1/imports", {"import_type": import_type, "file_id": file["id"]})
    assert created.status_code == 201, created.text
    return dict(created.json())


@ops_task
async def validate(env: Env, tenant: Tenant, import_id: str, settings: Any = None) -> str:
    async with storage.s3_client(env.settings) as s3:
        return await processing.run_validation(
            env.db, settings or env.settings, s3, tenant.id, uuid.UUID(import_id)
        )


@ops_task
async def commit(env: Env, tenant: Tenant, import_id: str, settings: Any = None) -> str:
    async with storage.s3_client(env.settings) as s3:
        return await processing.run_commit(
            env.db, settings or env.settings, s3, tenant.id, uuid.UUID(import_id)
        )


async def get(api: Api, import_id: str) -> dict[str, Any]:
    response = await api.get(f"/v1/imports/{import_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


# --- the dry run -------------------------------------------------------------------------


async def test_dry_run_produces_a_downloadable_error_file(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    store.reject_names = {"Chandra"}
    created = await stage(
        api,
        env,
        tenant,
        csv_bytes(
            "asha@x.test,Asha,2026-01-05",
            "not-an-email,Bala,2026-01-06",
            ",Chandra,2026-01-07",
            "dev@x.test,Dev,",
        ),
    )
    assert created["status"] == "pending"
    assert any(j[0] == "pickwise.validate_import" for j in env.jobs)

    assert await validate(env, tenant, created["id"]) == "validated"
    done = await get(api, created["id"])
    assert done["status"] == "validated"
    stats = done["stats"]
    assert (stats["rows"], stats["valid_rows"], stats["error_rows"]) == (4, 2, 2)
    assert stats["errors_total"] == 4
    assert stats["errors_truncated"] is False
    assert done["has_error_file"] is True
    assert store.committed == {}  # a dry run writes nothing

    link = (await api.get(f"/v1/imports/{created['id']}/errors")).json()
    served = await fetch(link["url"])
    assert served.status_code == 200
    lines = served.text.strip().splitlines()
    assert lines[0] == "row,column,message"
    # Row numbers are the spreadsheet's (header = 1), and every problem is listed.
    assert "3,email,isn't an email address" in lines
    assert "4,email,is required" in lines
    assert any(line.startswith("4,name,") for line in lines)
    # Formula-looking messages are neutralised for spreadsheets.
    assert any("'=HYPERLINK" in line for line in lines)
    assert not any(line.split(",", 2)[-1].startswith("=") for line in lines)

    # A dry run with errors can't be committed.
    refused = await api.post(
        f"/v1/imports/{created['id']}/commit", {"row_version": done["row_version"]}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "import_has_errors"


async def test_a_clean_dry_run_has_no_error_file_and_can_be_committed_in_batches(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    lines = [f"p{i}@x.test,Person {i},2026-01-0{i + 1}" for i in range(5)]
    created = await stage(api, env, tenant, csv_bytes(*lines))
    await validate(env, tenant, created["id"])
    validated = await get(api, created["id"])
    assert validated["stats"]["error_rows"] == 0
    assert validated["has_error_file"] is False
    assert (await api.get(f"/v1/imports/{created['id']}/errors")).status_code == 404

    started = await api.post(
        f"/v1/imports/{created['id']}/commit", {"row_version": validated["row_version"]}
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "committing"
    assert any(j[0] == "pickwise.commit_import" for j in env.jobs)
    # Stale versions are conflicts; so is committing twice.
    assert (
        await api.post(
            f"/v1/imports/{created['id']}/commit", {"row_version": validated["row_version"]}
        )
    ).status_code == 409

    small = env.settings.model_copy(update={"imports_batch_size": 2})
    assert await commit(env, tenant, created["id"], small) == "committed"
    final = await get(api, created["id"])
    assert final["status"] == "committed"
    assert final["stats"]["committed_rows"] == 5
    assert store.batches == [2, 2, 1]
    assert store.committed["p3@x.test"] == "Person 3"
    ((audited,),) = await env.sql(
        "SELECT count(*) FROM audit.events WHERE tenant_id = :t AND action = 'import.committed' "
        "AND entity_id = :i",
        t=tenant.id,
        i=uuid.UUID(created["id"]),
    )
    assert audited == 1
    # Running the job again does nothing.
    assert await commit(env, tenant, created["id"]) == "skipped"


async def test_the_commit_is_tied_to_the_dry_runs_file(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    created = await stage(api, env, tenant, csv_bytes("a@x.test,Asha,"))
    await validate(env, tenant, created["id"])
    version = (await get(api, created["id"]))["row_version"]
    # The recorded hash of the file no longer matches what was validated.
    await env.sql(
        "UPDATE platform.files SET sha256 = digest('tampered', 'sha256') WHERE id = :f",
        f=uuid.UUID(created["file_id"]),
    )
    refused = await api.post(f"/v1/imports/{created['id']}/commit", {"row_version": version})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "file_changed"
    assert store.committed == {}


async def test_the_worker_refuses_bytes_that_differ_from_the_dry_run(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    created = await stage(api, env, tenant, csv_bytes("a@x.test,Asha,"))
    await validate(env, tenant, created["id"])
    version = (await get(api, created["id"]))["row_version"]
    await api.post(f"/v1/imports/{created['id']}/commit", {"row_version": version})
    await env.sql(
        'UPDATE platform.imports SET stats = stats || \'{"file_sha256": "00"}\' WHERE id = :i',
        i=uuid.UUID(created["id"]),
    )
    assert await commit(env, tenant, created["id"]) == "failed"
    failed = await get(api, created["id"])
    assert failed["stats"]["error"] == "file_changed"
    assert store.committed == {}


async def test_rows_are_checked_again_just_before_they_are_written(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    created = await stage(api, env, tenant, csv_bytes("a@x.test,Asha,", "b@x.test,Bala,"))
    await validate(env, tenant, created["id"])
    version = (await get(api, created["id"]))["row_version"]
    await api.post(f"/v1/imports/{created['id']}/commit", {"row_version": version})
    store.reject_names = {"Bala"}  # the world changed between the dry run and the commit
    assert await commit(env, tenant, created["id"]) == "failed"
    failed = (await get(api, created["id"]))["stats"]
    assert failed["error"] == "revalidation_failed"
    assert failed["first_bad_row"] == 3
    assert store.committed == {}  # the batch with the bad row wasn't written


async def test_a_failed_commit_can_be_retried_and_never_leaks_details(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    created = await stage(api, env, tenant, csv_bytes("a@x.test,Asha,", "b@x.test,Bala,"))
    await validate(env, tenant, created["id"])
    version = (await get(api, created["id"]))["row_version"]
    await api.post(f"/v1/imports/{created['id']}/commit", {"row_version": version})
    store.fail_commit = True
    assert await commit(env, tenant, created["id"]) == "failed"
    failed = await get(api, created["id"])
    assert failed["stats"]["error"] == "commit_failed"
    assert "secret detail" not in str(failed)

    store.fail_commit = False
    retry = await api.post(
        f"/v1/imports/{created['id']}/commit", {"row_version": failed["row_version"]}
    )
    assert retry.status_code == 200, retry.text
    assert await commit(env, tenant, created["id"]) == "committed"
    assert set(store.committed) == {"a@x.test", "b@x.test"}


async def test_xlsx_files_are_imported_too(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Email", "Name", "Start Date"])
    sheet.append(["a@x.test", "Asha", "2026-01-05"])
    out = io.BytesIO()
    book.save(out)
    created = await stage(api, env, tenant, out.getvalue(), name="people.xlsx", mime=XLSX)
    assert await validate(env, tenant, created["id"]) == "validated"
    assert (await get(api, created["id"]))["stats"]["rows"] == 1


async def test_file_level_problems_fail_the_import_with_a_readable_reason(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    created = await stage(api, env, tenant, b"email,name,salary\na@x.test,Asha,100\n")
    assert await validate(env, tenant, created["id"]) == "failed"
    failed = (await get(api, created["id"]))["stats"]
    assert failed["error"] == "invalid_file"
    assert "Unknown columns: salary" in failed["error_message"]
    # Too many rows.
    created = await stage(api, env, tenant, csv_bytes("a@x.test,A,", "b@x.test,B,"))
    capped = env.settings.model_copy(update={"imports_max_rows": 1})
    assert await validate(env, tenant, created["id"], capped) == "failed"
    assert "more than 1 rows" in (await get(api, created["id"]))["stats"]["error_message"]


async def test_the_error_file_is_capped(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    created = await stage(api, env, tenant, csv_bytes(*[f"bad{i},N," for i in range(5)]))
    capped = env.settings.model_copy(update={"imports_max_errors": 2})
    await validate(env, tenant, created["id"], capped)
    stats = (await get(api, created["id"]))["stats"]
    assert (stats["errors_total"], stats["errors_truncated"], stats["error_rows"]) == (5, True, 5)
    link = (await api.get(f"/v1/imports/{created['id']}/errors")).json()
    assert len((await fetch(link["url"])).text.strip().splitlines()) == 3  # header + 2


# --- rules around it ---------------------------------------------------------------------


async def test_unscanned_or_wrong_files_cannot_start_an_import(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    pending = await upload(api, env, csv_bytes("a@x.test,A,"), "people.csv", CSV)
    early = await api.post("/v1/imports", {"import_type": "people_test", "file_id": pending["id"]})
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "file_not_ready"
    text_file = await upload(api, env, b"just text\n", "notes.txt", "text/plain")
    await env.scan_file(tenant.id, uuid.UUID(text_file["id"]))
    wrong = await api.post(
        "/v1/imports", {"import_type": "people_test", "file_id": text_file["id"]}
    )
    assert wrong.status_code == 422
    unknown = await api.post("/v1/imports", {"import_type": "nope_test", "file_id": pending["id"]})
    assert unknown.status_code == 422
    missing = await api.post(
        "/v1/imports", {"import_type": "people_test", "file_id": str(uuid.uuid4())}
    )
    assert missing.status_code == 404


async def test_each_type_needs_its_own_permission(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    file = await upload(api, env, b"email\na@x.test\n", "people.csv", CSV)
    await env.scan_file(tenant.id, uuid.UUID(file["id"]))
    denied = await api.post(
        "/v1/imports", {"import_type": "restricted_test", "file_id": file["id"]}
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"
    listed = {t["key"]: t["permitted"] for t in (await api.get("/v1/imports/types")).json()}
    assert listed["people_test"] is True
    assert listed["restricted_test"] is False


async def test_creation_is_idempotent(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    file = await upload(api, env, csv_bytes("a@x.test,A,"), "people.csv", CSV)
    await env.scan_file(tenant.id, uuid.UUID(file["id"]))
    body = {"import_type": "people_test", "file_id": file["id"]}
    headers = {"Idempotency-Key": "import-1"}
    first = await api.post("/v1/imports", body, headers=headers)
    second = await api.post("/v1/imports", body, headers=headers)
    assert first.json()["id"] == second.json()["id"]
    assert len((await api.get("/v1/imports")).json()["items"]) == 1


async def test_who_can_see_an_import(
    hr: tuple[Api, Account],
    make_api: MakeApi,
    env: Env,
    tenant: Tenant,
    store: Store,
) -> None:
    api, _ = hr
    created = await stage(api, env, tenant, csv_bytes("bad,A,"))
    await validate(env, tenant, created["id"])
    # Another person who may import sees neither the import nor its error file ...
    other = await (await make_api()).sign_in(await env.add_account(tenant, "hr_ops"))
    assert (await other.get(f"/v1/imports/{created['id']}")).status_code == 404
    assert (await other.get(f"/v1/imports/{created['id']}/errors")).status_code == 404
    assert (await other.get("/v1/imports")).json()["items"] == []
    # ... a tenant admin (read_all) sees both ...
    admin = await (await make_api()).sign_in(tenant.admin)
    assert (await admin.get(f"/v1/imports/{created['id']}")).status_code == 200
    assert (await admin.get(f"/v1/imports/{created['id']}/errors")).status_code == 200
    # ... and an employee can't use imports at all.
    employee = await (await make_api()).sign_in(await env.add_account(tenant, "employee"))
    assert (await employee.get("/v1/imports")).status_code == 403
    # Other tenants see nothing.
    stranger_tenant = await env.create_tenant()
    stranger = await (await make_api()).sign_in(stranger_tenant.admin)
    assert (await stranger.get(f"/v1/imports/{created['id']}")).status_code == 404


async def test_cancel(hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store) -> None:
    api, _ = hr
    created = await stage(api, env, tenant, csv_bytes("a@x.test,A,"))
    cancelled = await api.post(
        f"/v1/imports/{created['id']}/cancel", {"row_version": created["row_version"]}
    )
    assert cancelled.json()["status"] == "cancelled"
    assert await validate(env, tenant, created["id"]) == "skipped"
    again = await api.post(
        f"/v1/imports/{created['id']}/cancel", {"row_version": cancelled.json()["row_version"]}
    )
    assert again.status_code == 409


async def test_the_sweep_requeues_lost_dry_runs_and_fails_stuck_imports(
    hr: tuple[Api, Account], env: Env, tenant: Tenant, store: Store
) -> None:
    api, _ = hr
    lost = await stage(api, env, tenant, csv_bytes("a@x.test,A,"))
    stuck = await stage(api, env, tenant, csv_bytes("b@x.test,B,"))
    # created_at and updated_at are maintained by triggers, so age is faked with the thresholds.
    await env.sql(
        "UPDATE platform.imports SET status = 'committing', stats = '{\"phase\": \"commit\"}' "
        "WHERE id = :i",
        i=uuid.UUID(stuck["id"]),
    )

    @ops_task
    async def sweep() -> tuple[list[tuple[uuid.UUID, uuid.UUID]], int]:
        async with env.db.ops_session() as session:
            return await processing.sweep(session, pending_after_seconds=-5, stale_after_minutes=-1)

    pending, failed = await sweep()
    assert (tenant.id, uuid.UUID(lost["id"])) in pending
    assert failed >= 1
    after = (await get(api, stuck["id"]))["stats"]
    assert after["error"] == "timed_out"
    assert after["phase"] == "commit"  # so a retry is allowed
