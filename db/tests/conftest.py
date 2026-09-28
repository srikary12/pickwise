# SPDX-License-Identifier: AGPL-3.0-only
"""SQL-level tests. They connect as the real login users of the test stack."""

import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
import pytest

PASSWORD_ENV = {
    "pickwise_migrator": "PG_MIGRATOR_PASSWORD",
    "pickwise_api": "PG_API_PASSWORD",
    "pickwise_worker": "PG_WORKER_PASSWORD",
    "pickwise_maint": "PG_MAINT_PASSWORD",
}

Connect = Callable[[str], psycopg.Connection[tuple[object, ...]]]


@pytest.fixture
def connect() -> Iterator[Connect]:
    opened: list[psycopg.Connection[tuple[object, ...]]] = []

    def _connect(user: str) -> psycopg.Connection[tuple[object, ...]]:
        conn = psycopg.connect(
            host=os.environ.get("DATABASE_HOST", "postgres"),
            port=int(os.environ.get("DATABASE_PORT", "5432")),
            dbname=os.environ.get("DATABASE_NAME", "pickwise"),
            user=user,
            password=os.environ[PASSWORD_ENV[user]],
            autocommit=True,
        )
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        conn.close()


Conn = psycopg.Connection[tuple[object, ...]]


@contextmanager
def as_tenant(
    conn: Conn,
    tenant_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
    actor_type: str = "user",
    request_id: str = "test-request",
) -> Iterator[Conn]:
    """A transaction with the request context set exactly as tenant_session() does."""
    with conn.transaction():
        conn.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.user_id', %s, true), "
            "set_config('app.actor_type', %s, true), set_config('app.request_id', %s, true), "
            "set_config('app.client_ip', '203.0.113.7', true)",
            (
                str(tenant_id) if tenant_id else "",
                str(user_id) if user_id else "",
                actor_type,
                request_id,
            ),
        )
        yield conn


@contextmanager
def as_ops(conn: Conn, purge_mode: bool = False) -> Iterator[Conn]:
    """A transaction as pickwise_ops (the worker/maint login must SET LOCAL ROLE)."""
    with conn.transaction():
        conn.execute("SET LOCAL ROLE pickwise_ops")
        if purge_mode:
            conn.execute("SELECT set_config('app.purge_mode', 'on', true)")
        yield conn


@dataclass(frozen=True)
class SeededTenant:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    membership_id: uuid.UUID
    role_id: uuid.UUID
    file_id: uuid.UUID


TEST_PERMISSION = "test.fixture.read"


def seed_tenant(maint: Conn, label: str) -> SeededTenant:
    """One tenant with a user, membership, role, grant and file, created as ops
    (the way provisioning will). Every row goes through the real triggers."""
    suffix = uuid.uuid4().hex[:8]
    with as_ops(maint):
        maint.execute(
            "INSERT INTO platform.permissions (code, module, description) VALUES (%s, 'test', 'fixture') "
            "ON CONFLICT (code) DO NOTHING",
            (TEST_PERMISSION,),
        )
        tenant_id = _one(
            maint.execute(
                "INSERT INTO platform.tenants (slug, name, status) VALUES (%s, %s, 'active') RETURNING id",
                (f"{label}-{suffix}", f"Tenant {label.upper()}"),
            )
        )
        user_id = _one(
            maint.execute(
                "INSERT INTO platform.users (email, display_name) VALUES (%s, %s) RETURNING id",
                (f"{label}-{suffix}@example.test", f"User {label}"),
            )
        )
        maint.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.actor_type', 'system', true)",
            (str(tenant_id),),
        )
        membership_id = _one(
            maint.execute(
                "INSERT INTO platform.memberships (user_id, status) VALUES (%s, 'active') RETURNING id",
                (user_id,),
            )
        )
        role_id = _one(
            maint.execute(
                "INSERT INTO platform.roles (key, name) VALUES ('tenant_admin', 'Tenant admin') RETURNING id"
            )
        )
        maint.execute(
            "INSERT INTO platform.role_permissions (role_id, permission_code) VALUES (%s, %s)",
            (role_id, TEST_PERMISSION),
        )
        maint.execute(
            "INSERT INTO platform.role_assignments (membership_id, role_id, scope_type) "
            "VALUES (%s, %s, 'tenant')",
            (membership_id, role_id),
        )
        file_id = _one(
            maint.execute(
                "INSERT INTO platform.files (storage_key, bucket, original_name) "
                "VALUES (%s, 'pickwise-files', 'offer.pdf') RETURNING id",
                (f"{tenant_id}/{uuid.uuid4()}",),
            )
        )
    return SeededTenant(tenant_id, user_id, membership_id, role_id, file_id)


def purge(maint: Conn, tenant_id: uuid.UUID) -> list[tuple[object, ...]]:
    with as_ops(maint):
        maint.execute(
            "UPDATE platform.tenants SET status = 'closed', closed_at = now() WHERE id = %s",
            (tenant_id,),
        )
        return maint.execute("SELECT * FROM platform.purge_tenant(%s)", (tenant_id,)).fetchall()


def _one(cursor: psycopg.Cursor[tuple[object, ...]]) -> uuid.UUID:
    row = cursor.fetchone()
    assert row is not None
    value = row[0]
    assert isinstance(value, uuid.UUID)
    return value


@pytest.fixture
def two_tenants(connect: Connect) -> Iterator[tuple[SeededTenant, SeededTenant]]:
    maint = connect("pickwise_maint")
    a = seed_tenant(maint, "acme")
    b = seed_tenant(maint, "globex")
    yield a, b
    for t in (a, b):
        exists = None
        with as_ops(maint):
            exists = maint.execute(
                "SELECT 1 FROM platform.tenants WHERE id = %s", (t.tenant_id,)
            ).fetchone()
        if exists:
            purge(maint, t.tenant_id)
