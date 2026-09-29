# SPDX-License-Identifier: AGPL-3.0-only
"""Tenant purge (ADR 0002) and the partition helper (ADR 0003)."""

import psycopg
import pytest
from conftest import Connect, SeededTenant, as_ops, purge

pytestmark = pytest.mark.db

_TENANT_TABLES = """
    SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('platform', 'audit', 'core', 'leave', 'attendance', 'payroll', 'ai', 'recruit')
      AND c.relkind IN ('r', 'p') AND NOT c.relispartition
      AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id')
"""


def test_purge_removes_every_row_of_the_tenant_and_nothing_else(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    maint = connect("pickwise_maint")
    with as_ops(maint):
        # A session pointing at A (a non-tenant_id FK into tenants) must be unlinked, not block the purge.
        maint.execute(
            "INSERT INTO platform.sessions (user_id, active_tenant_id, token_hash, expires_at) "
            "VALUES (%s, %s, gen_random_bytes(32), now() + interval '1 hour')",
            (a.user_id, a.tenant_id),
        )
        tables = [str(r[0]) for r in maint.execute(_TENANT_TABLES).fetchall()]
        b_before = {t: _count(maint, t, b.tenant_id) for t in tables}

    report = {str(name): int(str(n)) for name, n in purge(maint, a.tenant_id)}

    with as_ops(maint):
        leftovers = {t: n for t in tables if (n := _count(maint, t, a.tenant_id))}
        b_after = {t: _count(maint, t, b.tenant_id) for t in tables}
        tenant_row = maint.execute(
            "SELECT 1 FROM platform.tenants WHERE id = %s", (a.tenant_id,)
        ).fetchone()
        session_tenant = maint.execute(
            "SELECT active_tenant_id FROM platform.sessions WHERE user_id = %s", (a.user_id,)
        ).fetchone()
    assert leftovers == {}
    assert tenant_row is None
    assert b_after == b_before
    assert session_tenant == (None,)
    assert report["platform.tenants"] == 1
    assert report["platform.memberships"] == 1
    assert report["audit.events"] > 0


def _count(conn: psycopg.Connection[tuple[object, ...]], table: str, tenant_id: object) -> int:
    row = conn.execute(
        f"SELECT count(*) FROM {table} WHERE tenant_id = %s", (tenant_id,)
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


def test_purge_refuses_an_open_tenant(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    maint = connect("pickwise_maint")
    with pytest.raises(psycopg.errors.RaiseException, match="not closed"), as_ops(maint):
        maint.execute("SELECT * FROM platform.purge_tenant(%s)", (a.tenant_id,))


def test_purge_refuses_to_run_without_the_ops_role(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    worker = connect("pickwise_worker")  # has pickwise_ops membership but hasn't SET ROLE
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker.execute("SELECT * FROM platform.purge_tenant(%s)", (a.tenant_id,))


def test_ensure_monthly_partitions_is_idempotent_and_children_are_locked_down(
    connect: Connect,
) -> None:
    worker = connect("pickwise_worker")
    with as_ops(worker):
        first = worker.execute(
            "SELECT platform.ensure_monthly_partitions('audit.events', 6, 1)"
        ).fetchone()
        second = worker.execute(
            "SELECT platform.ensure_monthly_partitions('audit.events', 6, 1)"
        ).fetchone()
    assert first is not None
    assert second == (0,)
    api = connect("pickwise_api")
    children = [
        str(r[0])
        for r in api.execute(
            "SELECT inhrelid::regclass::text FROM pg_inherits WHERE inhparent = 'audit.events'::regclass"
        ).fetchall()
    ]
    assert len(children) >= 8  # one month back + current + six ahead
    for child in children:
        row = api.execute(
            "SELECT relrowsecurity, relforcerowsecurity, "
            "(SELECT count(*) FROM pg_policies p WHERE p.schemaname = n.nspname AND p.tablename = c.relname), "
            "has_table_privilege('pickwise_app', c.oid, 'SELECT') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = %s::regclass",
            (child,),
        ).fetchone()
        assert row == (True, True, 3, False), child


def test_ensure_monthly_partitions_rejects_non_partitioned_tables(connect: Connect) -> None:
    worker = connect("pickwise_worker")
    with (
        pytest.raises(psycopg.errors.RaiseException, match="not a partitioned table"),
        as_ops(worker),
    ):
        worker.execute("SELECT platform.ensure_monthly_partitions('platform.roles')")
