# SPDX-License-Identifier: AGPL-3.0-only
"""tenant_session() / ops_session() and the model mixins, through the real app driver (asyncpg)."""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr
from sqlalchemy import String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.exc import StaleDataError

from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database, OpsSessionNotAllowedError, ops_command, ops_task
from pickwise.shared.models import AuditColumnsMixin, Base, TenantMixin, VersionedMixin
from pickwise.shared.settings import Settings

pytestmark = pytest.mark.db


class Role(TenantMixin, AuditColumnsMixin, VersionedMixin, Base):
    """Exercises the mixins against a real table."""

    __tablename__ = "roles"
    __table_args__ = {"schema": "platform"}  # noqa: RUF012
    key: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)


def _database(user: str, password_env: str) -> Database:
    settings = Settings(database_user=user, database_password=SecretStr(os.environ[password_env]))
    return Database.from_settings(settings)


@pytest.fixture
async def api_db() -> AsyncIterator[Database]:
    db = _database("pickwise_api", "PG_API_PASSWORD")
    yield db
    await db.dispose()


@pytest.fixture
async def worker_db() -> AsyncIterator[Database]:
    db = _database("pickwise_worker", "PG_WORKER_PASSWORD")
    yield db
    await db.dispose()


@pytest.fixture
async def tenant_id(worker_db: Database) -> AsyncIterator[uuid.UUID]:
    """A throwaway tenant created and purged through ops sessions."""

    @ops_task
    async def create() -> uuid.UUID:
        async with worker_db.ops_session() as s:
            value = await s.scalar(
                text(
                    "INSERT INTO platform.tenants (slug, name, status) VALUES (:s, 'Session test', 'active') "
                    "RETURNING id"
                ),
                {"s": f"sess-{uuid.uuid4().hex[:8]}"},
            )
            assert isinstance(value, uuid.UUID)
            return value

    @ops_task
    async def remove(tid: uuid.UUID) -> None:
        async with worker_db.ops_session() as s:
            await s.execute(
                text(
                    "UPDATE platform.tenants SET status = 'closed', closed_at = now() WHERE id = :t"
                ),
                {"t": tid},
            )
            await s.execute(text("SELECT * FROM platform.purge_tenant(:t)"), {"t": tid})

    tid = await create()
    yield tid
    await remove(tid)


async def test_tenant_session_sets_every_context_value(
    api_db: Database, tenant_id: uuid.UUID
) -> None:
    user_id = uuid.uuid4()
    ctx = RequestContext(
        ActorType.USER, tenant_id, user_id, request_id="req-ctx", client_ip="192.0.2.1"
    )
    async with api_db.tenant_session(ctx) as session:
        row = (
            await session.execute(
                text(
                    "SELECT platform.current_tenant_id(), platform.current_user_id(), "
                    "current_setting('app.actor_type'), current_setting('app.request_id'), "
                    "current_setting('app.client_ip'), current_user"
                )
            )
        ).one()
    assert tuple(row) == (tenant_id, user_id, "user", "req-ctx", "192.0.2.1", "pickwise_api")


async def test_pooled_connection_reuse_does_not_leak_context(tenant_id: uuid.UUID) -> None:
    settings = Settings(
        database_user="pickwise_api", database_password=SecretStr(os.environ["PG_API_PASSWORD"])
    )
    db = Database.from_settings(settings)
    try:
        async with db.tenant_session(RequestContext(ActorType.USER, tenant_id)) as s:
            assert await s.scalar(text("SELECT count(*) FROM platform.tenants")) == 1
        # The pool hands the same connection back; context must be gone, with no cast error.
        async with db.engine.connect() as conn:
            assert await conn.scalar(text("SELECT platform.current_tenant_id()")) is None
            assert await conn.scalar(text("SELECT count(*) FROM platform.tenants")) == 0
    finally:
        await db.dispose()


async def test_ops_session_is_refused_outside_ops_entry_points(worker_db: Database) -> None:
    with pytest.raises(OpsSessionNotAllowedError):
        async with worker_db.ops_session():
            pass


async def test_ops_session_runs_as_pickwise_ops_with_the_right_actor(worker_db: Database) -> None:
    @ops_task
    async def as_task() -> tuple[object, ...]:
        async with worker_db.ops_session() as s:
            return tuple(
                (
                    await s.execute(text("SELECT current_user, current_setting('app.actor_type')"))
                ).one()
            )

    @ops_command
    async def as_command() -> tuple[object, ...]:
        async with worker_db.ops_session() as s:
            return tuple(
                (
                    await s.execute(text("SELECT current_user, current_setting('app.actor_type')"))
                ).one()
            )

    assert await as_task() == ("pickwise_ops", "worker")
    assert await as_command() == ("pickwise_ops", "system")


async def test_versioned_mixin_detects_stale_updates(
    api_db: Database, tenant_id: uuid.UUID
) -> None:
    ctx = RequestContext(ActorType.USER, tenant_id)
    async with api_db.tenant_session(ctx) as s:
        role = Role(key="payroll_admin", name="Payroll admin")
        s.add(role)
        await s.flush()
        await s.refresh(role)
        role_id = role.id
        assert (role.tenant_id, role.row_version) == (tenant_id, 1)

    async with api_db.tenant_session(ctx) as s:
        role = await s.get_one(Role, (tenant_id, role_id))
        role.name = "Payroll admins"
        await s.flush()
        assert role.row_version == 2

    # Someone else updates first; our copy still says row_version 2.
    async def lost_update() -> None:
        async with api_db.tenant_session(ctx) as s:
            stale = await s.get_one(Role, (tenant_id, role_id))
            await s.execute(
                text("UPDATE platform.roles SET name = 'changed elsewhere' WHERE id = :i"),
                {"i": role_id},
            )
            stale.name = "lost update"
            await s.flush()

    with pytest.raises(StaleDataError):
        await lost_update()
