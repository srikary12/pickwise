# SPDX-License-Identifier: AGPL-3.0-only
"""Tenant isolation through RLS and composite keys, as the real app login sees it."""

import psycopg
import pytest
from conftest import Connect, SeededTenant, as_ops, as_tenant

pytestmark = pytest.mark.db


def test_no_context_sees_nothing(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    api = connect("pickwise_api")
    for table in ("platform.tenants", "platform.memberships", "platform.roles", "audit.events"):
        row = api.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert row == (0,), table


def test_tenant_sees_only_its_own_rows(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        tenants = api.execute("SELECT id FROM platform.tenants").fetchall()
        memberships = api.execute("SELECT id FROM platform.memberships").fetchall()
        roles = api.execute("SELECT tenant_id FROM platform.roles").fetchall()
    assert tenants == [(a.tenant_id,)]
    assert memberships == [(a.membership_id,)]
    assert roles == [(a.tenant_id,)]


def test_cannot_update_or_delete_another_tenants_rows(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        updated = api.execute(
            "UPDATE platform.roles SET name = 'pwned' WHERE id = %s", (b.role_id,)
        ).rowcount
        deleted = api.execute("DELETE FROM platform.files WHERE id = %s", (b.file_id,)).rowcount
    assert (updated, deleted) == (0, 0)
    with as_tenant(api, b.tenant_id):
        name = api.execute("SELECT name FROM platform.roles WHERE id = %s", (b.role_id,)).fetchone()
        file_ = api.execute("SELECT 1 FROM platform.files WHERE id = %s", (b.file_id,)).fetchone()
    assert name == ("Tenant admin",)
    assert file_ == (1,)


def test_insert_with_a_foreign_tenant_id_fails_with_check(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    api = connect("pickwise_api")
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"),
        as_tenant(api, a.tenant_id),
    ):
        api.execute(
            "INSERT INTO platform.roles (tenant_id, key, name) VALUES (%s, 'sneaky', 'x')",
            (b.tenant_id,),
        )


def test_composite_fk_blocks_a_cross_tenant_reference(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    api = connect("pickwise_api")
    # A's role assignment pointing at B's role: (tenant_id=A, role_id=B.role) doesn't exist.
    with pytest.raises(psycopg.errors.ForeignKeyViolation), as_tenant(api, a.tenant_id):
        api.execute(
            "INSERT INTO platform.role_assignments (membership_id, role_id, scope_type) "
            "VALUES (%s, %s, 'tenant')",
            (a.membership_id, b.role_id),
        )


def test_context_does_not_leak_to_the_next_transaction_on_a_reused_connection(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id):
        assert api.execute("SELECT count(*) FROM platform.memberships").fetchone() == (1,)
    # Same connection, next transaction: the setting reads '' now, not NULL.
    raw = api.execute("SELECT current_setting('app.tenant_id', true)").fetchone()
    assert raw == ("",)
    assert api.execute("SELECT platform.current_tenant_id()").fetchone() == (None,)
    assert api.execute("SELECT count(*) FROM platform.memberships").fetchone() == (0,)


@pytest.mark.parametrize("user", ["pickwise_worker", "pickwise_maint"])
def test_ops_capable_logins_see_all_tenants_only_after_set_role(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant], user: str
) -> None:
    a, b = two_tenants
    conn = connect(user)
    ids = {a.tenant_id, b.tenant_id}
    if user == "pickwise_worker":
        # Holds pickwise_app's privileges, so RLS applies and no context means no rows.
        before = {r[0] for r in conn.execute("SELECT id FROM platform.tenants").fetchall()}
        assert before == set()
    else:
        # pickwise_maint has no privileges of its own until it becomes pickwise_ops.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT id FROM platform.tenants")
    with as_ops(conn):
        after = {r[0] for r in conn.execute("SELECT id FROM platform.tenants").fetchall()}
    assert ids <= after
