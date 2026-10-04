# SPDX-License-Identifier: AGPL-3.0-only
"""Erasing a data subject: the runner, purge mode, and the platform handler."""

import uuid

import pytest
from sqlalchemy import text

from pickwise.platform import storage
from pickwise.platform.erasure import (
    ERASURE_HANDLERS,
    ErasureContext,
    ErasureHandlerRegistry,
    ErasureReport,
    ErasureSubject,
    run_erasure,
)
from pickwise.platform.erasure.platform_handler import erase_platform_data
from pickwise.platform.notifications.service import notify
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database, ops_task
from tests.integration.conftest import MakeApi
from tests.integration.support import Account, Api, Env, Tenant
from tests.integration.test_files import PDF, object_exists, tenant_file_key, upload

pytestmark = pytest.mark.db


@ops_task
async def erase(
    env: Env,
    subject: ErasureSubject,
    *,
    request_id: uuid.UUID | None = None,
    registry: ErasureHandlerRegistry = ERASURE_HANDLERS,
) -> ErasureReport:
    async with storage.s3_client(env.settings) as s3, env.db.ops_session() as session:
        return await run_erasure(session, s3, subject, request_id=request_id, registry=registry)


def platform_only() -> ErasureHandlerRegistry:
    registry = ErasureHandlerRegistry()
    registry.register("platform", erase_platform_data)
    return registry


class Scene:
    """An employee with data in every platform table the handler covers, next to a colleague
    whose data must survive."""

    def __init__(self) -> None:
        self.subject_id = uuid.uuid4()
        self.subject: Account
        self.colleague: Account
        self.file_id: str
        self.other_file_id: str
        self.key: str
        self.consent_id: uuid.UUID
        self.request_id: uuid.UUID


@pytest.fixture
async def scene(env: Env, tenant: Tenant, make_api: MakeApi, api_db: Database) -> Scene:
    s = Scene()
    s.subject = await env.add_account(tenant, "employee")
    s.colleague = await env.add_account(tenant, "employee")
    api: Api = await (await make_api()).sign_in(s.subject)

    file = await upload(
        api, env, PDF, owner_entity_type="employee", owner_entity_id=str(s.subject_id)
    )
    other = await upload(
        api, env, PDF, owner_entity_type="employee", owner_entity_id=str(uuid.uuid4())
    )
    for f in (file, other):
        await env.scan_file(tenant.id, uuid.UUID(f["id"]))
    s.file_id, s.other_file_id = file["id"], other["id"]
    s.key = await tenant_file_key(env, s.file_id)

    ((notice_id,),) = await env.sql(
        "INSERT INTO platform.privacy_notices (tenant_id, purpose, version, body_md, published_at) "
        "VALUES (:t, 'employment', 1, 'notice', now()) RETURNING id",
        t=tenant.id,
    )
    ((consent_id,),) = await env.sql(
        "INSERT INTO platform.consents (tenant_id, subject_type, subject_id, purpose, notice_id, "
        "method, evidence) VALUES (:t, 'employee', :s, 'employment', :n, 'portal', "
        "CAST(:e AS jsonb)) RETURNING id",
        t=tenant.id,
        s=s.subject_id,
        n=notice_id,
        e='{"ip": "203.0.113.9", "user_agent": "Firefox"}',
    )
    s.consent_id = consent_id
    ((request_id,),) = await env.sql(
        "INSERT INTO platform.data_subject_requests (tenant_id, subject_type, subject_id, "
        "request_type, status, due_at, notes) VALUES (:t, 'employee', :s, 'erasure', "
        "'in_progress', now() + interval '30 days', 'Phoned from a personal number') "
        "RETURNING id",
        t=tenant.id,
        s=s.subject_id,
    )
    s.request_id = request_id

    ctx = RequestContext(ActorType.USER, tenant.id, tenant.admin.user_id)
    async with api_db.tenant_session(ctx) as session:
        await notify(
            session,
            env.kek,
            tenant_id=tenant.id,
            user_ids=[s.subject.user_id, s.colleague.user_id],
            type_key="system.notice",
            title="Welcome",
            link="/home",
        )
        await notify(
            session,
            env.kek,
            tenant_id=tenant.id,
            user_ids=[tenant.admin.user_id],
            type_key="system.notice",
            title="About the employee",
            entity_type="employee",
            entity_id=s.subject_id,
        )
    return s


def subject_of(tenant: Tenant, s: Scene, **extra: object) -> ErasureSubject:
    return ErasureSubject(
        tenant.id,
        "employee",
        s.subject_id,
        user_id=s.subject.user_id,
        email=s.subject.email,
        **extra,
    )


async def count(env: Env, sql: str, **params: object) -> int:
    rows = await env.sql(sql, **params)
    return int(rows[0][0])


async def test_platform_data_of_the_subject_is_erased_and_the_rest_is_kept(
    env: Env, tenant: Tenant, scene: Scene
) -> None:
    s = scene
    assert await object_exists(env.settings, env.settings.s3_bucket_files, s.key)
    audited_before = await count(
        env,
        "SELECT count(*) FROM audit.events WHERE entity_id = :i AND changes IS NOT NULL",
        i=uuid.UUID(s.file_id),
    )
    assert audited_before >= 1

    report = await erase(env, subject_of(tenant, s), request_id=s.request_id)

    assert report.counts["platform"] == {
        "files": 1,
        "consents": 1,
        "notifications": 2,  # their own, and the one about them
        "emails": 1,
        "data_subject_requests": 1,
    }
    assert report.audit_rows_scrubbed >= audited_before

    # The file: bytes gone from storage, the row stays but forgets the name and hash.
    assert not await object_exists(env.settings, env.settings.s3_bucket_files, s.key)
    ((name, sha, purged),) = await env.sql(
        "SELECT original_name, sha256, purged_at FROM platform.files WHERE id = :i",
        i=uuid.UUID(s.file_id),
    )
    assert (name, sha) == ("[erased]", None)
    assert purged is not None
    # Someone else's file is untouched.
    other_key = await tenant_file_key(env, s.other_file_id)
    assert await object_exists(env.settings, env.settings.s3_bucket_files, other_key)

    # Consent: the fact stays, the evidence goes.
    ((evidence, granted),) = await env.sql(
        "SELECT evidence, granted_at FROM platform.consents WHERE id = :i", i=s.consent_id
    )
    assert evidence == {}
    assert granted is not None

    # Notifications and mail to the subject are gone; the colleague's stay.
    assert (
        await count(
            env,
            "SELECT count(*) FROM platform.notifications WHERE user_id = :u",
            u=s.subject.user_id,
        )
        == 0
    )
    assert (
        await count(
            env,
            "SELECT count(*) FROM platform.notifications WHERE entity_id = :e",
            e=s.subject_id,
        )
        == 0
    )
    assert (
        await count(
            env,
            "SELECT count(*) FROM platform.notifications WHERE user_id = :u",
            u=s.colleague.user_id,
        )
        == 1
    )
    assert (
        await count(
            env,
            "SELECT count(*) FROM platform.email_outbox WHERE to_address = :e",
            e=s.subject.email,
        )
        == 0
    )
    assert (
        await count(
            env,
            "SELECT count(*) FROM platform.email_outbox WHERE to_address = :e",
            e=s.colleague.email,
        )
        == 1
    )

    # The request record stays, completed, without its free-text notes.
    ((status, completed, notes),) = await env.sql(
        "SELECT status, completed_at, notes FROM platform.data_subject_requests WHERE id = :i",
        i=s.request_id,
    )
    assert status == "completed"
    assert completed is not None
    assert notes is None

    # No residual personal data in the audit trail of the erased rows ...
    assert (
        await count(
            env,
            "SELECT count(*) FROM audit.events WHERE entity_id = :i AND changes IS NOT NULL",
            i=uuid.UUID(s.file_id),
        )
        == 0
    )
    # ... and one semantic event says an erasure happened, with counts only.
    ((details,),) = await env.sql(
        "SELECT changes FROM audit.events WHERE tenant_id = :t AND action = 'erasure.completed'",
        t=tenant.id,
    )
    assert details["subject_type"] == "employee"
    assert details["handlers"] == {"platform": 6}
    assert str(s.subject_id) not in str(details)
    assert s.subject.email not in str(details)


async def test_erasing_twice_is_harmless(env: Env, tenant: Tenant, scene: Scene) -> None:
    await erase(env, subject_of(tenant, scene))
    second = await erase(env, subject_of(tenant, scene))
    assert second.counts["platform"] == {
        "files": 0,
        "consents": 0,
        "notifications": 0,
        "emails": 0,
        "data_subject_requests": 0,
    }


async def test_another_tenants_data_is_never_touched(
    env: Env, tenant: Tenant, scene: Scene
) -> None:
    other = await env.create_tenant()
    ((notice,),) = await env.sql(
        "INSERT INTO platform.privacy_notices (tenant_id, purpose, version, body_md) "
        "VALUES (:t, 'employment', 1, 'n') RETURNING id",
        t=other.id,
    )
    # The same subject id in another tenant.
    await env.sql(
        "INSERT INTO platform.consents (tenant_id, subject_type, subject_id, purpose, notice_id, "
        "method, evidence) VALUES (:t, 'employee', :s, 'employment', :n, 'portal', "
        'CAST(\'{"ip": "198.51.100.1"}\' AS jsonb))',
        t=other.id,
        s=scene.subject_id,
        n=notice,
    )
    await erase(env, subject_of(tenant, scene))
    ((evidence,),) = await env.sql(
        "SELECT evidence FROM platform.consents WHERE tenant_id = :t", t=other.id
    )
    assert evidence == {"ip": "198.51.100.1"}


async def test_a_failing_handler_rolls_everything_back_and_leaves_purge_mode(
    env: Env, tenant: Tenant, scene: Scene
) -> None:
    registry = platform_only()

    async def broken(ctx: ErasureContext) -> dict[str, int]:
        raise RuntimeError("the module's data is in a state we can't handle")

    registry.register("later_module", broken, order=1000)
    with pytest.raises(RuntimeError, match="can't handle"):
        await erase(env, subject_of(tenant, scene), registry=registry)
    # Nothing was erased: the platform handler's work rolled back with the failure.
    assert (
        await count(
            env,
            "SELECT count(*) FROM platform.notifications WHERE user_id = :u",
            u=scene.subject.user_id,
        )
        == 1
    )
    # (Storage deletes can't be undone by a rollback, which is why a re-run must finish the job.)
    ((purged,),) = await env.sql(
        "SELECT purged_at FROM platform.files WHERE id = :i", i=uuid.UUID(scene.file_id)
    )
    assert purged is None
    ((status,),) = await env.sql(
        "SELECT status FROM platform.data_subject_requests WHERE id = :i", i=scene.request_id
    )
    assert status == "in_progress"
    # A later run, with the module fixed, completes.
    registry.unregister("later_module")
    report = await erase(env, subject_of(tenant, scene), registry=registry)
    assert report.counts["platform"]["notifications"] == 2


async def test_handlers_run_in_order_and_their_scrub_targets_are_scrubbed(
    env: Env, tenant: Tenant, scene: Scene
) -> None:
    calls: list[str] = []
    registry = ErasureHandlerRegistry()

    async def first(ctx: ErasureContext) -> dict[str, int]:
        calls.append("first")
        # A module deleting from an append-only table: only purge mode allows it.
        deleted = (
            await ctx.session.execute(
                text(
                    "DELETE FROM audit.events WHERE tenant_id = :t AND entity_id = :i RETURNING 1"
                ),
                {"t": ctx.subject.tenant_id, "i": uuid.UUID(scene.other_file_id)},
            )
        ).all()
        return {"audit_rows_deleted": len(deleted)}

    async def second(ctx: ErasureContext) -> dict[str, int]:
        calls.append("second")
        ctx.scrub("platform.files", uuid.UUID(scene.file_id))
        return {}

    registry.register("b_second", second, order=20)
    registry.register("a_first", first, order=10)
    report = await erase(env, subject_of(tenant, scene), registry=registry)
    assert calls == ["first", "second"]
    assert report.counts["a_first"]["audit_rows_deleted"] >= 1
    assert report.audit_rows_scrubbed >= 1


async def test_the_runner_needs_an_ops_session(
    env: Env, tenant: Tenant, scene: Scene, api_db: Database
) -> None:
    ctx = RequestContext(ActorType.USER, tenant.id, tenant.admin.user_id)
    async with storage.s3_client(env.settings) as s3, api_db.tenant_session(ctx) as session:
        with pytest.raises(Exception, match="permission denied for function begin_erasure"):
            await run_erasure(session, s3, subject_of(tenant, scene))


async def test_the_request_must_match_the_subject(env: Env, tenant: Tenant, scene: Scene) -> None:
    stranger = ErasureSubject(tenant.id, "employee", uuid.uuid4())
    with pytest.raises(Exception, match="No erasure request matches"):
        await erase(env, stranger, request_id=scene.request_id)
    # And a request of another type isn't an erasure request.
    ((access,),) = await env.sql(
        "INSERT INTO platform.data_subject_requests (tenant_id, subject_type, subject_id, "
        "request_type, due_at) VALUES (:t, 'employee', :s, 'access', now() + interval '30 days') "
        "RETURNING id",
        t=tenant.id,
        s=scene.subject_id,
    )
    with pytest.raises(Exception, match="No erasure request matches"):
        await erase(env, subject_of(tenant, scene), request_id=access)
