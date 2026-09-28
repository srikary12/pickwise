# SPDX-License-Identifier: AGPL-3.0-only
"""Pre-tenant SECURITY DEFINER functions (CLAUDE.md rule 4a)."""

import datetime
import hashlib
import uuid

import psycopg
import pytest
from conftest import Connect, SeededTenant, as_ops, as_tenant

pytestmark = pytest.mark.db


def test_list_memberships_for_user_works_without_context(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    maint = connect("pickwise_maint")
    # Give A's user a second membership, in tenant B.
    with as_ops(maint):
        maint.execute(
            "INSERT INTO platform.memberships (tenant_id, user_id, status) VALUES (%s, %s, 'invited')",
            (b.tenant_id, a.user_id),
        )
    api = connect("pickwise_api")
    rows = api.execute(
        "SELECT tenant_id, tenant_slug, tenant_name, tenant_status, membership_status "
        "FROM platform.list_memberships_for_user(%s)",
        (a.user_id,),
    ).fetchall()
    assert {(r[0], r[4]) for r in rows} == {(a.tenant_id, "active"), (b.tenant_id, "invited")}
    assert {r[2] for r in rows} == {"Tenant ACME", "Tenant GLOBEX"}
    # …while the tables themselves stay invisible without context.
    assert api.execute("SELECT count(*) FROM platform.memberships").fetchone() == (0,)
    assert api.execute("SELECT count(*) FROM platform.tenants").fetchone() == (0,)


def test_list_memberships_returns_nothing_for_other_users(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    api = connect("pickwise_api")
    rows = api.execute(
        "SELECT * FROM platform.list_memberships_for_user(%s)", (uuid.uuid4(),)
    ).fetchall()
    assert rows == []


def test_resolve_invite_reports_state_without_revealing_the_email(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    now = datetime.datetime.now(datetime.UTC)
    tokens = {
        "valid": (now + datetime.timedelta(days=1), None),
        "expired": (now - datetime.timedelta(minutes=1), None),
        "used": (now + datetime.timedelta(days=1), now),
    }
    hashes = {}
    for name, (expires, used) in tokens.items():
        hashes[name] = hashlib.sha256(f"{name}-{uuid.uuid4()}".encode()).digest()
        api.execute(
            "INSERT INTO platform.auth_tokens (purpose, token_hash, email, tenant_id, expires_at, used_at) "
            "VALUES ('invite', %s, 'new.hire@example.test', %s, %s, %s)",
            (hashes[name], a.tenant_id, expires, used),
        )
    results = {}
    for name, token_hash in hashes.items():
        cur = api.execute("SELECT * FROM platform.resolve_invite(%s)", (token_hash,))
        assert cur.description is not None
        assert [c.name for c in cur.description] == [
            "token_id",
            "tenant_id",
            "user_id",
            "has_email",
            "is_expired",
            "is_used",
        ]
        row = cur.fetchone()
        assert row is not None
        results[name] = (row[1], row[3], row[4], row[5])
    assert results == {
        "valid": (a.tenant_id, True, False, False),
        "expired": (a.tenant_id, True, True, False),
        "used": (a.tenant_id, True, False, True),
    }
    # The tokens carry tenant A, so the fixture's purge removes them.


def test_log_platform_event_writes_a_tenantless_row_the_app_cannot_read(connect: Connect) -> None:
    api = connect("pickwise_api")
    row = api.execute(
        "SELECT audit.log_platform_event('login.failed', NULL, 'req-login-1', '198.51.100.9')"
    ).fetchone()
    assert row is not None
    event_id = row[0]
    assert api.execute(
        "SELECT count(*) FROM audit.events WHERE id = %s", (event_id,)
    ).fetchone() == (0,)
    maint = connect("pickwise_maint")
    with as_ops(maint):
        stored = maint.execute(
            "SELECT tenant_id, actor_type, action, request_id, changes FROM audit.events WHERE id = %s",
            (event_id,),
        ).fetchone()
    assert stored == (None, "system", "login.failed", "req-login-1", None)


def test_log_platform_event_rejects_free_text_actions(connect: Connect) -> None:
    api = connect("pickwise_api")
    with pytest.raises(psycopg.errors.RaiseException):
        api.execute(
            "SELECT audit.log_platform_event('user bob@example.com failed', NULL, NULL, NULL)"
        )


@pytest.mark.parametrize(
    ("call", "user"),
    [
        ("SELECT platform.ensure_monthly_partitions('audit.events')", "pickwise_api"),
        ("SELECT * FROM platform.purge_tenant(gen_random_uuid())", "pickwise_api"),
        ("SELECT audit.scrub_subject('platform.roles', gen_random_uuid())", "pickwise_api"),
        ("SELECT platform.apply_tenant_policies('platform')", "pickwise_api"),
        ("SELECT platform.apply_rls('platform.roles')", "pickwise_worker"),
    ],
)
def test_ops_and_migration_functions_are_not_executable_by_the_app(
    connect: Connect, call: str, user: str
) -> None:
    conn = connect(user)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(call)


def test_scrub_subject_needs_purge_mode(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    maint = connect("pickwise_maint")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="purge mode"), as_ops(maint):
        maint.execute("SELECT audit.scrub_subject('platform.memberships', %s)", (a.membership_id,))
    with as_ops(maint, purge_mode=True):
        scrubbed = maint.execute(
            "SELECT audit.scrub_subject('platform.memberships', %s)", (a.membership_id,)
        ).fetchone()
        remaining = maint.execute(
            "SELECT count(*) FROM audit.events WHERE entity_table = 'memberships' "
            "AND entity_id = %s AND changes IS NOT NULL",
            (a.membership_id,),
        ).fetchone()
    assert scrubbed is not None
    assert int(str(scrubbed[0])) >= 1
    assert remaining == (0,)
    # The app still sees the (now empty) audit trail for its tenant.
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id):
        row = api.execute(
            "SELECT count(*) FROM audit.events WHERE entity_id = %s", (a.membership_id,)
        ).fetchone()
    assert row is not None
    assert int(str(row[0])) >= 1
