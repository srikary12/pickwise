# SPDX-License-Identifier: AGPL-3.0-only
"""Searching and exporting the audit trail."""

import csv
import datetime
import io
import uuid
from typing import Any

import pytest

from pickwise.platform.audit_api import service
from tests.integration.conftest import MakeApi
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db


@pytest.fixture
async def admin(make_api: MakeApi, tenant: Tenant) -> Api:
    return await (await make_api()).sign_in(tenant.admin)


async def make_fields(admin: Api, count: int) -> list[str]:
    ids = []
    for i in range(count):
        created = await admin.post(
            "/v1/custom-fields",
            {
                "entity_type": "employee",
                "key": f"f_{i}",
                "label": f"Field {i}",
                "field_type": "text",
            },
        )
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])
    return ids


async def events(admin: Api, query: str = "") -> list[dict[str, Any]]:
    response = await admin.get(f"/v1/audit/events?limit=200{query}")
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


async def test_filters(admin: Api, tenant: Tenant) -> None:
    field_ids = await make_fields(admin, 2)
    mine = await events(admin, f"&actor_user_id={tenant.admin.user_id}")
    assert mine
    assert all(e["actor_user_id"] == str(tenant.admin.user_id) for e in mine)
    assert mine[0]["actor_name"]

    semantic = await events(admin, "&action=custom_field.created")
    assert {e["entity_id"] for e in semantic} == set(field_ids)
    by_prefix = await events(admin, "&action=custom_field.*")
    assert {e["action"] for e in by_prefix} == {"custom_field.created"}
    row_changes = await events(admin, "&entity_table=platform.custom_field_definitions")
    assert {e["action"] for e in row_changes} >= {"custom_field.created"}
    one = await events(admin, f"&entity_id={field_ids[0]}")
    assert one
    assert {e["entity_id"] for e in one} == {field_ids[0]}
    assert await events(admin, f"&entity_id={uuid.uuid4()}") == []

    now = datetime.datetime.now(datetime.UTC)
    soon = (now + datetime.timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    before = (now - datetime.timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    assert await events(admin, f"&since={soon}") == []
    assert await events(admin, f"&until={before}") == []
    assert await events(admin, f"&since={before}&until={soon}")

    request_id = semantic[0]["request_id"]
    assert request_id
    same_request = await events(admin, f"&request_id={request_id}")
    assert semantic[0]["id"] in {e["id"] for e in same_request}


async def test_bad_filters_are_rejected(admin: Api) -> None:
    for query in ("entity_table=nope", "action=Bad Action", "actor_user_id=x", "cursor=%25%25"):
        response = await admin.get(f"/v1/audit/events?{query}")
        assert response.status_code == 422, query


async def test_keyset_pagination_covers_every_event_once(admin: Api) -> None:
    await make_fields(admin, 6)
    everything = await events(admin)
    seen: list[str] = []
    cursor: str | None = None
    while True:
        url = "/v1/audit/events?limit=4" + (f"&cursor={cursor}" if cursor else "")
        page = (await admin.get(url)).json()
        assert len(page["items"]) <= 4
        seen += [e["id"] for e in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [e["id"] for e in everything]
    assert len(set(seen)) == len(seen)
    times = [e["occurred_at"] for e in everything]
    assert times == sorted(times, reverse=True)


async def test_diffs_never_hold_confidential_values(admin: Api, env: Env, tenant: Tenant) -> None:
    await env.add_account(tenant, "employee", email=f"secret.person@{tenant.slug}.test")
    changes = await events(admin, "&entity_table=platform.memberships")
    assert changes
    assert f"secret.person@{tenant.slug}.test" not in str(changes)
    notifications = await events(admin, "&entity_table=platform.users")
    assert f"secret.person@{tenant.slug}.test" not in str(notifications)


async def test_permissions(make_api: MakeApi, env: Env, tenant: Tenant, admin: Api) -> None:
    hr = await (await make_api()).sign_in(await env.add_account(tenant, "hr_admin"))
    assert (await hr.get("/v1/audit/events")).status_code == 200  # may read ...
    assert (await hr.get("/v1/audit/events/export")).status_code == 403  # ... but not export
    for role in ("employee", "manager", "payroll_admin", "recruiter"):
        person = await (await make_api()).sign_in(await env.add_account(tenant, role))
        assert (await person.get("/v1/audit/events")).status_code == 403, role


async def test_tenants_see_only_their_own_events(
    make_api: MakeApi, env: Env, admin: Api, tenant: Tenant
) -> None:
    await make_fields(admin, 1)
    other = await env.create_tenant()
    other_admin = await (await make_api()).sign_in(other.admin)
    theirs = await events(other_admin)
    assert theirs
    assert not {e["id"] for e in theirs} & {e["id"] for e in await events(admin)}
    assert all(e["action"] != "custom_field.created" for e in theirs)


async def test_export_streams_csv_and_is_audited_first(
    admin: Api, tenant: Tenant, env: Env
) -> None:
    await make_fields(admin, 3)
    response = await admin.get("/v1/audit/events/export?action=custom_field.*")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="audit-events.csv"' in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][:5] == ["occurred_at", "actor_user_id", "actor_type", "action", "entity"]
    assert len(rows) == 4  # header + 3 events
    assert {r[3] for r in rows[1:]} == {"custom_field.created"}

    ((details,),) = await env.sql(
        "SELECT changes FROM audit.events WHERE tenant_id = :t AND action = 'export' "
        "ORDER BY occurred_at DESC LIMIT 1",
        t=tenant.id,
    )
    assert details["kind"] == "audit_events"
    assert details["rows"] == 3
    assert details["action"] == "custom_field.*"


def test_cells_that_look_like_formulas_are_quoted() -> None:
    from pickwise.shared.csvsafe import safe_cell

    assert [safe_cell(v) for v in ("=1+1", "+1", "-1", "@x", "\tx", "plain", "")] == [
        "'=1+1",
        "'+1",
        "'-1",
        "'@x",
        "'\tx",
        "plain",
        "",
    ]


async def test_export_is_formula_safe_and_size_limited(
    admin: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await admin.post(
        "/v1/custom-fields",
        {"entity_type": "employee", "key": "f_x", "label": "=SUM(A1)", "field_type": "text"},
    )
    assert created.status_code == 201
    text = (
        await admin.get("/v1/audit/events/export?entity_table=platform.custom_field_definitions")
    ).text
    assert not any(line.startswith("=") for line in text.splitlines())
    monkeypatch.setattr(service, "MAX_EXPORT_ROWS", 1)
    refused = await admin.get("/v1/audit/events/export")
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "export_too_large"
