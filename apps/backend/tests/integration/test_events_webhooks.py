# SPDX-License-Identifier: AGPL-3.0-only
"""The event outbox, the relay and signed webhook deliveries."""

import datetime
import json
import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from pickwise.platform.events import EVENT_HANDLERS, OutboxEvent, emit_event
from pickwise.platform.events.relay import MAX_ATTEMPTS as EVENT_MAX_ATTEMPTS
from pickwise.platform.webhooks import delivery
from pickwise.platform.webhooks.signing import verify
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database
from tests.integration.conftest import MakeApi
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db

PUBLIC_IP = "93.184.216.34"
URL = "https://hooks.example.com/pickwise"


@pytest.fixture(autouse=True)
def public_dns(app: FastAPI) -> Iterator[None]:
    """hooks.example.com resolves to a public address unless a test says otherwise."""

    async def resolve(host: str, port: int) -> list[str]:
        return [PUBLIC_IP]

    app.state.webhook_resolver = resolve
    yield
    del app.state.webhook_resolver


@pytest.fixture
async def admin(make_api: MakeApi, tenant: Tenant) -> Api:
    return await (await make_api()).sign_in(tenant.admin)


async def create_endpoint(
    admin: Api, url: str = URL, event_types: list[str] | None = None
) -> tuple[str, str]:
    response = await admin.post(
        "/v1/webhooks/endpoints", {"url": url, "event_types": event_types or []}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["id"], body["secret"]


async def emit(db: Database, tenant: Tenant, event_type: str, **payload: object) -> uuid.UUID:
    ctx = RequestContext(ActorType.USER, tenant.id, tenant.admin.user_id)
    async with db.tenant_session(ctx) as session:
        return await emit_event(
            session,
            aggregate_type="leave_request",
            aggregate_id=uuid.uuid4(),
            event_type=event_type,
            payload=dict(payload),
        )


def receiver(
    seen: list[httpx.Request], *, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, headers=headers or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def deliveries_of(env: Env, tenant: Tenant) -> list[tuple[uuid.UUID, str, int, str | None]]:
    rows = await env.sql(
        "SELECT id, status, attempt, last_error FROM platform.webhook_deliveries "
        "WHERE tenant_id = :t ORDER BY id",
        t=tenant.id,
    )
    return [(r[0], r[1], r[2], r[3]) for r in rows]


# --- the outbox and the relay -------------------------------------------------


async def test_emitted_events_are_published_by_the_relay_and_fan_out_to_matching_endpoints(
    admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    everything, _ = await create_endpoint(admin)
    leave_only, _ = await create_endpoint(admin, event_types=["leave.*"])
    exact, _ = await create_endpoint(admin, event_types=["payroll.run.finalized"])

    event_id = await emit(api_db, tenant, "leave.request.approved", days=2)
    result = await env.relay()

    assert result.published == 1
    delivered_to = {
        r[0]
        for r in await env.sql(
            "SELECT endpoint_id FROM platform.webhook_deliveries WHERE event_id = :e", e=event_id
        )
    }
    assert delivered_to == {uuid.UUID(everything), uuid.UUID(leave_only)}
    assert uuid.UUID(exact) not in delivered_to
    assert {d for _, d in result.deliveries} == {
        r[0]
        for r in await env.sql(
            "SELECT id FROM platform.webhook_deliveries WHERE event_id = :e", e=event_id
        )
    }
    # Once published, an event is not relayed again.
    assert (await env.relay()).published == 0


async def test_rolled_back_transactions_emit_nothing(
    env: Env, api_db: Database, tenant: Tenant
) -> None:
    ctx = RequestContext(ActorType.USER, tenant.id, tenant.admin.user_id)

    async def change_then_fail() -> None:
        async with api_db.tenant_session(ctx) as session:
            await emit_event(
                session,
                aggregate_type="x",
                aggregate_id=uuid.uuid4(),
                event_type="leave.request.approved",
            )
            raise RuntimeError("the business change failed")

    with pytest.raises(RuntimeError):
        await change_then_fail()
    rows = await env.sql(
        "SELECT count(*) FROM platform.outbox_events WHERE tenant_id = :t", t=tenant.id
    )
    assert rows == [(0,)]


async def test_event_types_must_be_dotted_lowercase(api_db: Database, tenant: Tenant) -> None:
    for bad in ("Leave.Approved", "approved", "leave..x", "leave.request-approved"):
        with pytest.raises(ValueError, match="bad event type"):
            await emit(api_db, tenant, bad)


async def test_in_process_handlers_run_in_the_events_tenant(
    env: Env, api_db: Database, tenant: Tenant
) -> None:
    other = await env.create_tenant()
    await emit(api_db, other, "leave.request.approved")  # must not leak into the handler's view
    seen: list[tuple[OutboxEvent, uuid.UUID, int]] = []

    async def handler(session, event: OutboxEvent) -> None:  # type: ignore[no-untyped-def]
        context = (await session.execute(text("SELECT platform.current_tenant_id()"))).scalar_one()
        events = (
            await session.execute(text("SELECT count(*) FROM platform.outbox_events"))
        ).scalar_one()
        seen.append((event, context, events))

    EVENT_HANDLERS.register("test.seen", "leave.*", handler)
    try:
        event_id = await emit(api_db, tenant, "leave.request.approved", days=1)
        await env.relay()
    finally:
        EVENT_HANDLERS.unregister("test.seen")

    mine = [s for s in seen if s[0].id == event_id]
    assert len(mine) == 1
    event, context, visible = mine[0]
    assert event.tenant_id == context == tenant.id
    assert event.payload == {"days": 1}
    # RLS applied to the handler: it saw this tenant's events only, never the other tenant's.
    assert visible == 1


async def test_a_failing_handler_holds_the_event_back_until_an_operator_retries_it(
    admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    endpoint, _ = await create_endpoint(admin)
    calls = 0

    async def broken(session, event: OutboxEvent) -> None:  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        raise RuntimeError("boom with secret-looking detail")

    EVENT_HANDLERS.register("test.broken", "payroll.*", broken)
    try:
        event_id = await emit(api_db, tenant, "payroll.run.finalized")
        for _ in range(EVENT_MAX_ATTEMPTS + 3):
            await env.relay()
        assert calls == EVENT_MAX_ATTEMPTS  # the relay stops trying after the cap
        ((published, attempts),) = await env.sql(
            "SELECT published_at, attempts FROM platform.outbox_events WHERE id = :e", e=event_id
        )
        assert published is None
        assert attempts == EVENT_MAX_ATTEMPTS
        assert await deliveries_of(env, tenant) == []  # nothing was sent to customers

        # An operator fixes the handler and retries through the API.
        EVENT_HANDLERS.unregister("test.broken")
        assert (await admin.post(f"/v1/webhooks/events/{event_id}/retry")).status_code == 204
        assert (await env.relay()).published == 1
        assert len(await deliveries_of(env, tenant)) == 1
        # An event that isn't stuck can't be "retried".
        assert (await admin.post(f"/v1/webhooks/events/{event_id}/retry")).status_code == 404
    finally:
        EVENT_HANDLERS.unregister("test.broken")
    assert endpoint


# --- delivery -------------------------------------------------------------------


async def test_a_delivery_is_signed_and_pinned_to_the_checked_address(
    admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    _, secret = await create_endpoint(admin)
    event_id = await emit(api_db, tenant, "leave.request.approved", days=2)
    ((tenant_id, delivery_id),) = (await env.relay()).deliveries
    seen: list[httpx.Request] = []
    now = datetime.datetime.now(datetime.UTC)

    async with receiver(seen) as client:
        status = await env.deliver(tenant_id, delivery_id, client, resolver=_public, now=now)

    assert status == "succeeded"
    (request,) = seen
    # Connected to the address that passed the check; the Host header names the real host.
    assert request.url.host == PUBLIC_IP
    assert request.headers["host"] == "hooks.example.com"
    assert request.extensions["sni_hostname"] == "hooks.example.com"
    assert request.headers["x-pickwise-event"] == "leave.request.approved"
    assert request.headers["x-pickwise-delivery"] == str(delivery_id)
    assert verify(
        secret, request.content, request.headers["x-pickwise-signature"], now=now.timestamp()
    )
    body = json.loads(request.content)
    assert body["id"] == str(event_id)
    assert body["type"] == "leave.request.approved"
    assert body["tenant_id"] == str(tenant.id)
    assert body["data"] == {"days": 2}
    ((_, state, attempts, error),) = await deliveries_of(env, tenant)
    assert (state, attempts, error) == ("succeeded", 1, None)
    # A finished delivery is never sent twice.
    async with receiver(seen) as client:
        assert await env.deliver(tenant_id, delivery_id, client, resolver=_public) == "skipped"
    assert len(seen) == 1


async def _public(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


async def test_failures_back_off_then_dead_letter_and_can_be_replayed(
    admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    await create_endpoint(admin)
    await emit(api_db, tenant, "leave.request.approved")
    ((tenant_id, delivery_id),) = (await env.relay()).deliveries
    seen: list[httpx.Request] = []
    clock = datetime.datetime.now(datetime.UTC)

    statuses = []
    for _ in range(delivery.MAX_ATTEMPTS):
        async with receiver(seen, status=500) as client:
            statuses.append(
                await env.deliver(tenant_id, delivery_id, client, resolver=_public, now=clock)
            )
        ((next_at, error),) = await env.sql(
            "SELECT next_attempt_at, last_error FROM platform.webhook_deliveries WHERE id = :d",
            d=delivery_id,
        )
        assert error == "HTTP 500"
        if next_at is not None:
            # Not due yet: the sweep leaves it alone, a premature attempt is skipped.
            async with receiver(seen, status=500) as client:
                assert (
                    await env.deliver(tenant_id, delivery_id, client, resolver=_public, now=clock)
                    == "skipped"
                )
            clock = next_at + datetime.timedelta(seconds=1)
    assert statuses == ["pending"] * (delivery.MAX_ATTEMPTS - 1) + ["dead"]
    assert len(seen) == delivery.MAX_ATTEMPTS

    page = (await admin.get("/v1/webhooks/deliveries?status=dead")).json()
    assert [i["id"] for i in page["items"]] == [str(delivery_id)]
    assert page["items"][0]["attempt"] == delivery.MAX_ATTEMPTS

    replay = await admin.post(f"/v1/webhooks/deliveries/{delivery_id}/replay")
    assert replay.status_code == 201, replay.text
    assert replay.json()["status"] == "pending"
    assert replay.json()["event_id"] == page["items"][0]["event_id"]
    # A delivery that is still pending can't be replayed again.
    again = await admin.post(f"/v1/webhooks/deliveries/{replay.json()['id']}/replay")
    assert again.status_code == 409
    async with receiver(seen) as client:
        assert (
            await env.deliver(tenant_id, uuid.UUID(replay.json()["id"]), client, resolver=_public)
            == "succeeded"
        )


async def test_redirects_are_not_followed(
    admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    await create_endpoint(admin)
    await emit(api_db, tenant, "leave.request.approved")
    ((tenant_id, delivery_id),) = (await env.relay()).deliveries
    seen: list[httpx.Request] = []
    async with receiver(
        seen, status=302, headers={"Location": "http://169.254.169.254/"}
    ) as client:
        assert await env.deliver(tenant_id, delivery_id, client, resolver=_public) == "pending"
    assert len(seen) == 1  # the Location was never requested
    ((_, _, _, error),) = await deliveries_of(env, tenant)
    assert error == "redirect refused"


async def test_a_host_that_turns_private_after_registration_is_blocked_at_delivery(
    admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    """DNS rebinding: the name resolved to a public address when it was registered."""
    await create_endpoint(admin)
    await emit(api_db, tenant, "leave.request.approved")
    ((tenant_id, delivery_id),) = (await env.relay()).deliveries
    seen: list[httpx.Request] = []

    async def rebinding(host: str, port: int) -> list[str]:
        return ["169.254.169.254"]

    async with receiver(seen) as client:
        status = await env.deliver(tenant_id, delivery_id, client, resolver=rebinding)
    assert status == "dead"  # a policy refusal isn't retried
    assert seen == []  # nothing was sent
    ((_, _, _, error),) = await deliveries_of(env, tenant)
    assert error is not None
    assert error.startswith("blocked:")


async def test_a_disabled_endpoint_gets_nothing(
    admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    endpoint_id, _ = await create_endpoint(admin)
    await emit(api_db, tenant, "leave.request.approved")
    ((tenant_id, delivery_id),) = (await env.relay()).deliveries
    current = (await admin.get(f"/v1/webhooks/endpoints/{endpoint_id}")).json()
    update = await admin.put(
        f"/v1/webhooks/endpoints/{endpoint_id}",
        {"url": URL, "event_types": [], "is_active": False, "row_version": current["row_version"]},
    )
    assert update.status_code == 200
    seen: list[httpx.Request] = []
    async with receiver(seen) as client:
        assert await env.deliver(tenant_id, delivery_id, client, resolver=_public) == "skipped"
    assert seen == []
    ((_, state, _, error),) = await deliveries_of(env, tenant)
    assert (state, error) == ("dead", "endpoint is disabled")
    # And new events skip it.
    await emit(api_db, tenant, "leave.request.approved")
    assert (await env.relay()).deliveries == []


# --- the API ----------------------------------------------------------------------


async def test_the_secret_is_shown_once_and_stored_encrypted(
    admin: Api, env: Env, tenant: Tenant
) -> None:
    endpoint_id, secret = await create_endpoint(admin)
    assert secret.startswith("whsec_")
    assert len(secret) > 40
    assert "secret" not in (await admin.get(f"/v1/webhooks/endpoints/{endpoint_id}")).json()
    listing = (await admin.get("/v1/webhooks/endpoints")).text
    assert secret not in listing
    ((stored,),) = await env.sql(
        "SELECT secret_enc FROM platform.webhook_endpoints WHERE id = :e", e=uuid.UUID(endpoint_id)
    )
    assert secret.encode() not in bytes(stored)


async def test_rotating_the_secret_changes_the_signature_key(
    admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    endpoint_id, old = await create_endpoint(admin)
    rotated = await admin.post(f"/v1/webhooks/endpoints/{endpoint_id}/rotate-secret")
    new = rotated.json()["secret"]
    assert new != old
    await emit(api_db, tenant, "leave.request.approved")
    ((tenant_id, delivery_id),) = (await env.relay()).deliveries
    seen: list[httpx.Request] = []
    now = datetime.datetime.now(datetime.UTC)
    async with receiver(seen) as client:
        await env.deliver(tenant_id, delivery_id, client, resolver=_public, now=now)
    header = seen[0].headers["x-pickwise-signature"]
    assert verify(new, seen[0].content, header, now=now.timestamp())
    assert not verify(old, seen[0].content, header, now=now.timestamp())


async def test_unsafe_urls_are_refused_at_registration(admin: Api, app: FastAPI) -> None:
    async def private(host: str, port: int) -> list[str]:
        return ["10.1.2.3"]

    for url in (
        "http://hooks.example.com/x",
        "https://user:pw@hooks.example.com/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest/meta-data",
    ):
        response = await admin.post("/v1/webhooks/endpoints", {"url": url})
        assert response.status_code == 422, url
        assert response.json()["error"]["code"] == "unsafe_webhook_target"
    app.state.webhook_resolver = private
    response = await admin.post("/v1/webhooks/endpoints", {"url": URL})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_webhook_target"
    # Event subscriptions are validated too.
    app.state.webhook_resolver = _public
    bad = await admin.post("/v1/webhooks/endpoints", {"url": URL, "event_types": ["Not An Event"]})
    assert bad.status_code == 422


async def test_updates_check_row_version_and_the_new_url(admin: Api, app: FastAPI) -> None:
    endpoint_id, _ = await create_endpoint(admin)
    current = (await admin.get(f"/v1/webhooks/endpoints/{endpoint_id}")).json()
    body = {"url": URL + "/v2", "event_types": ["leave.*"], "is_active": True}
    ok = await admin.put(
        f"/v1/webhooks/endpoints/{endpoint_id}", {**body, "row_version": current["row_version"]}
    )
    assert ok.status_code == 200
    assert ok.json()["row_version"] == current["row_version"] + 1
    stale = await admin.put(
        f"/v1/webhooks/endpoints/{endpoint_id}", {**body, "row_version": current["row_version"]}
    )
    assert stale.status_code == 409

    async def private(host: str, port: int) -> list[str]:
        return ["192.168.0.4"]

    app.state.webhook_resolver = private
    moved = await admin.put(
        f"/v1/webhooks/endpoints/{endpoint_id}",
        {**body, "url": "https://internal.example.com/", "row_version": ok.json()["row_version"]},
    )
    assert moved.status_code == 422


async def test_creation_is_idempotent_and_never_replays_the_secret(admin: Api) -> None:
    headers = {"Idempotency-Key": "create-hook-1"}
    first = await admin.post("/v1/webhooks/endpoints", {"url": URL}, headers=headers)
    second = await admin.post("/v1/webhooks/endpoints", {"url": URL}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert first.json()["secret"]
    assert second.json()["secret"] is None
    assert len((await admin.get("/v1/webhooks/endpoints")).json()) == 1


async def test_endpoints_are_limited_per_tenant(admin: Api) -> None:
    from pickwise.platform.webhooks.service import MAX_ENDPOINTS

    for _ in range(MAX_ENDPOINTS):
        await create_endpoint(admin)
    over = await admin.post("/v1/webhooks/endpoints", {"url": URL})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "too_many_endpoints"


async def test_another_tenant_sees_nothing(
    make_api: MakeApi, admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    endpoint_id, _ = await create_endpoint(admin)
    await emit(api_db, tenant, "leave.request.approved")
    ((_, delivery_id),) = (await env.relay()).deliveries
    other = await env.create_tenant()
    other_admin = await (await make_api()).sign_in(other.admin)
    assert (await other_admin.get("/v1/webhooks/endpoints")).json() == []
    assert (await other_admin.get(f"/v1/webhooks/endpoints/{endpoint_id}")).status_code == 404
    assert (await other_admin.get("/v1/webhooks/deliveries")).json()["items"] == []
    assert (await other_admin.get(f"/v1/webhooks/deliveries/{delivery_id}")).status_code == 404
    assert (
        await other_admin.post(f"/v1/webhooks/deliveries/{delivery_id}/replay")
    ).status_code == 404


async def test_only_tenant_admins_manage_webhooks(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    employee = await (await make_api()).sign_in(await env.add_account(tenant, "employee"))
    assert (await employee.get("/v1/webhooks/endpoints")).status_code == 403
    assert (await employee.post("/v1/webhooks/endpoints", {"url": URL})).status_code == 403
    assert (await employee.get("/v1/webhooks/deliveries")).status_code == 403


async def test_delivery_listing_filters_and_pages(
    admin: Api, env: Env, api_db: Database, tenant: Tenant
) -> None:
    first, _ = await create_endpoint(admin, event_types=["leave.*"])
    second, _ = await create_endpoint(admin, event_types=["payroll.*"])
    for _ in range(3):
        await emit(api_db, tenant, "leave.request.approved")
    await emit(api_db, tenant, "payroll.run.finalized")
    await env.relay()

    everything = (await admin.get("/v1/webhooks/deliveries?limit=2")).json()
    assert len(everything["items"]) == 2
    assert everything["next_cursor"]
    rest = (
        await admin.get(f"/v1/webhooks/deliveries?limit=10&before={everything['next_cursor']}")
    ).json()
    assert len(rest["items"]) == 2
    assert rest["next_cursor"] is None
    only_first = (await admin.get(f"/v1/webhooks/deliveries?endpoint_id={first}")).json()["items"]
    assert len(only_first) == 3
    assert {i["event_type"] for i in only_first} == {"leave.request.approved"}
    typed = (await admin.get("/v1/webhooks/deliveries?event_type=payroll.run.finalized")).json()
    assert [i["endpoint_id"] for i in typed["items"]] == [second]
    assert (await admin.get("/v1/webhooks/deliveries?status=dead")).json()["items"] == []
