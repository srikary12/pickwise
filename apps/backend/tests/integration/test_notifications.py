# SPDX-License-Identifier: AGPL-3.0-only
"""In-app notifications, channel preferences and the notification email."""

import uuid
from collections.abc import Iterator

import pytest

from pickwise.platform.notifications.registry import NOTIFICATION_TYPES, NotificationType
from pickwise.platform.notifications.service import effective_channels, notify
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database
from tests.integration.conftest import MakeApi
from tests.integration.support import Account, Api, Env, Tenant

pytestmark = pytest.mark.db

LOCKED = NotificationType("test.security", "Security", "Locked email", locked=("email",))
INAPP_ONLY = NotificationType("test.inapp", "In-app only", "No email", channels=("in_app",))


@pytest.fixture(autouse=True)
def test_types() -> Iterator[None]:
    NOTIFICATION_TYPES.register(LOCKED, INAPP_ONLY)
    yield
    NOTIFICATION_TYPES._types.pop(LOCKED.key, None)
    NOTIFICATION_TYPES._types.pop(INAPP_ONLY.key, None)


async def send(
    db: Database,
    env: Env,
    tenant: Tenant,
    users: list[Account],
    *,
    type_key: str = "system.notice",
    title: str = "Payslip ready",
    body: str | None = "Your October payslip is available.",
    link: str | None = "/payslips/2026-10",
) -> None:
    ctx = RequestContext(ActorType.USER, tenant.id, tenant.admin.user_id)
    async with db.tenant_session(ctx) as session:
        emails = await notify(
            session,
            env.kek,
            tenant_id=tenant.id,
            user_ids=[u.user_id for u in users],
            type_key=type_key,
            title=title,
            body=body,
            link=link,
        )
    for email in emails:
        await env.mailbox.dispatcher(email)


@pytest.fixture
async def person(make_api: MakeApi, env: Env, tenant: Tenant) -> tuple[Api, Account]:
    account = await env.add_account(tenant, "employee")
    return await (await make_api()).sign_in(account), account


async def test_in_app_and_email_by_default(
    person: tuple[Api, Account], api_db: Database, env: Env, tenant: Tenant
) -> None:
    api, account = person
    await send(api_db, env, tenant, [account])

    assert (await api.get("/v1/notifications/unread-count")).json() == {"unread": 1}
    page = (await api.get("/v1/notifications")).json()
    assert page["items"][0]["title"] == "Payslip ready"
    assert page["items"][0]["link"] == "/payslips/2026-10"
    assert page["next_cursor"] is None

    await env.mailbox.deliver()
    mail = next(m for m in reversed(env.mailbox.sent) if m.to == account.email)
    assert mail.subject == "Payslip ready"
    assert f"{env.settings.public_base_url}/payslips/2026-10" in mail.text
    # The content travels encrypted until it's sent.
    ((payload,),) = await env.sql(
        "SELECT payload::text FROM platform.email_outbox WHERE to_address = :a "
        "AND template_key = 'notification'",
        a=account.email,
    )
    assert "Payslip" not in payload


async def test_read_state(
    person: tuple[Api, Account], api_db: Database, env: Env, tenant: Tenant
) -> None:
    api, account = person
    for n in range(3):
        await send(api_db, env, tenant, [account], title=f"Note {n}")
    items = (await api.get("/v1/notifications")).json()["items"]
    assert [i["title"] for i in items] == ["Note 2", "Note 1", "Note 0"]  # newest first

    assert (await api.post(f"/v1/notifications/{items[0]['id']}/read")).status_code == 204
    assert (await api.get("/v1/notifications/unread-count")).json() == {"unread": 2}
    unread = (await api.get("/v1/notifications", params={"unread": "true"})).json()["items"]
    assert [i["title"] for i in unread] == ["Note 1", "Note 0"]
    assert (await api.post("/v1/notifications/read-all")).status_code == 204
    assert (await api.get("/v1/notifications/unread-count")).json() == {"unread": 0}


async def test_pagination(
    person: tuple[Api, Account], api_db: Database, env: Env, tenant: Tenant
) -> None:
    api, account = person
    for n in range(5):
        await send(api_db, env, tenant, [account], title=f"N{n}")
    first = (await api.get("/v1/notifications", params={"limit": 2})).json()
    assert [i["title"] for i in first["items"]] == ["N4", "N3"]
    second = (
        await api.get("/v1/notifications", params={"limit": 2, "before": first["next_cursor"]})
    ).json()
    assert [i["title"] for i in second["items"]] == ["N2", "N1"]
    last = (
        await api.get("/v1/notifications", params={"limit": 2, "before": second["next_cursor"]})
    ).json()
    assert [i["title"] for i in last["items"]] == ["N0"]
    assert last["next_cursor"] is None


async def test_notifications_are_private(
    make_api: MakeApi, person: tuple[Api, Account], api_db: Database, env: Env, tenant: Tenant
) -> None:
    api, account = person
    other_account = await env.add_account(tenant, "employee")
    other = await (await make_api()).sign_in(other_account)
    await send(api_db, env, tenant, [account])
    note_id = (await api.get("/v1/notifications")).json()["items"][0]["id"]
    assert (await other.get("/v1/notifications")).json()["items"] == []
    assert (await other.post(f"/v1/notifications/{note_id}/read")).status_code == 404
    assert (await api.get("/v1/notifications/unread-count")).json() == {"unread": 1}


async def test_preferences_choose_channels(
    person: tuple[Api, Account], api_db: Database, env: Env, tenant: Tenant
) -> None:
    api, account = person
    prefs = {p["type"]: p for p in (await api.get("/v1/notifications/preferences")).json()}
    assert prefs["system.notice"]["channels"] == {"in_app": True, "email": True}
    assert prefs["test.security"]["locked"] == ["email"]

    assert (
        await api.put(
            "/v1/notifications/preferences",
            {"preferences": {"system.notice": {"in_app": True, "email": False}}},
        )
    ).status_code == 204
    sent_before = env.mailbox.count_to(account.email)
    await send(api_db, env, tenant, [account])
    await env.mailbox.deliver()
    assert env.mailbox.count_to(account.email) == sent_before  # email is off
    assert (await api.get("/v1/notifications/unread-count")).json() == {"unread": 1}  # in-app on

    await api.put(
        "/v1/notifications/preferences",
        {"preferences": {"system.notice": {"in_app": False, "email": False}}},
    )
    await send(api_db, env, tenant, [account])
    assert (await api.get("/v1/notifications/unread-count")).json() == {"unread": 1}  # nothing new
    prefs = {p["type"]: p for p in (await api.get("/v1/notifications/preferences")).json()}
    assert prefs["system.notice"]["channels"] == {"in_app": False, "email": False}


async def test_locked_channels_and_unknown_types(person: tuple[Api, Account]) -> None:
    api, _ = person
    locked = await api.put(
        "/v1/notifications/preferences",
        {"preferences": {"test.security": {"in_app": True, "email": False}}},
    )
    assert locked.status_code == 422
    assert locked.json()["error"]["code"] == "channel_locked"
    unknown = await api.put(
        "/v1/notifications/preferences",
        {"preferences": {"nope.nothing": {"in_app": True, "email": True}}},
    )
    assert unknown.json()["error"]["code"] == "unknown_type"


async def test_a_type_without_email_sends_none(
    person: tuple[Api, Account], api_db: Database, env: Env, tenant: Tenant
) -> None:
    api, account = person
    before = env.mailbox.count_to(account.email)
    await send(api_db, env, tenant, [account], type_key="test.inapp")
    await env.mailbox.deliver()
    assert env.mailbox.count_to(account.email) == before
    assert (await api.get("/v1/notifications/unread-count")).json() == {"unread": 1}


async def test_only_active_members_are_notified(
    make_api: MakeApi, api_db: Database, env: Env, tenant: Tenant
) -> None:
    gone = await env.add_account(tenant, "employee", status="suspended")
    await send(api_db, env, tenant, [gone])
    ((count,),) = await env.sql(
        "SELECT count(*) FROM platform.notifications WHERE user_id = :u", u=gone.user_id
    )
    assert count == 0


async def test_untranslated_locales_fall_back_to_english(
    person: tuple[Api, Account], api_db: Database, env: Env, tenant: Tenant
) -> None:
    _, account = person
    await env.sql("UPDATE platform.users SET locale = 'hi-IN' WHERE id = :u", u=account.user_id)
    await send(api_db, env, tenant, [account], title="नमस्ते hello")
    await env.mailbox.deliver()
    mail = next(m for m in reversed(env.mailbox.sent) if m.to == account.email)
    assert "hello" in mail.text


async def test_links_must_be_app_paths(api_db: Database, env: Env, tenant: Tenant) -> None:
    for link in ("https://evil.example/x", "//evil.example/x", "javascript:alert(1)"):
        with pytest.raises(ValueError, match="app paths"):
            await send(api_db, env, tenant, [], link=link)
    with pytest.raises(ValueError, match="unknown notification type"):
        await send(api_db, env, tenant, [], type_key="no.such.type")


def test_effective_channels() -> None:
    plain = NotificationType("t.a", "A", "a")
    assert effective_channels(plain, {}) == {"in_app", "email"}
    assert effective_channels(plain, {"t.a": {"in_app": False, "email": True}}) == {"email"}
    assert effective_channels(plain, {"t.a": {"in_app": False, "email": False}}) == set()
    assert effective_channels(LOCKED, {"test.security": {"in_app": False, "email": False}}) == {
        "email"
    }
    assert effective_channels(plain, {"t.a": "garbage"}) == {"in_app", "email"}


async def test_requires_a_session(make_api: MakeApi) -> None:
    anonymous = await make_api()
    for path in (
        "/v1/notifications",
        "/v1/notifications/unread-count",
        "/v1/notifications/preferences",
    ):
        assert (await anonymous.get(path)).status_code == 401
    assert (await anonymous.post(f"/v1/notifications/{uuid.uuid4()}/read")).status_code == 401
