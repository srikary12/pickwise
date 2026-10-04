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


# --- Phase 2 lookups: resolve_api_key, resolve_tenant_by_slug, resolve_sso_by_domain ---------


def _api_key(
    maint: psycopg.Connection[tuple[object, ...]], tenant_id: uuid.UUID, **cols: str
) -> bytes:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    with as_ops(maint):
        maint.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        maint.execute(
            f"INSERT INTO platform.api_keys (name, prefix, key_hash, scopes{''.join(', ' + c for c in cols)}) "
            f"VALUES ('k', %s, %s, ARRAY['platform.users.read']{''.join(', ' + v for v in cols.values())})",
            (uuid.uuid4().hex[:8], digest),
        )
    return digest


def test_resolve_api_key_reports_usability(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    maint, api = connect("pickwise_maint"), connect("pickwise_api")
    good = _api_key(maint, a.tenant_id)
    revoked = _api_key(maint, a.tenant_id, revoked_at="now()")
    expired = _api_key(maint, a.tenant_id, expires_at="now() - interval '1 minute'")
    sql = "SELECT tenant_id, scopes, is_usable FROM platform.resolve_api_key(%s)"
    assert api.execute(sql, (good,)).fetchone() == (a.tenant_id, ["platform.users.read"], True)
    assert api.execute(sql, (revoked,)).fetchone() == (a.tenant_id, ["platform.users.read"], False)
    assert api.execute(sql, (expired,)).fetchone() == (a.tenant_id, ["platform.users.read"], False)
    assert api.execute(sql, (hashlib.sha256(b"unknown").digest(),)).fetchone() is None
    # The table itself stays invisible without a tenant context.
    assert api.execute("SELECT count(*) FROM platform.api_keys").fetchone() == (0,)


def test_resolve_api_key_is_false_for_a_suspended_tenant(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    maint, api = connect("pickwise_maint"), connect("pickwise_api")
    key = _api_key(maint, a.tenant_id)
    with as_ops(maint):
        maint.execute(
            "UPDATE platform.tenants SET status = 'suspended' WHERE id = %s", (a.tenant_id,)
        )
    row = api.execute("SELECT is_usable FROM platform.resolve_api_key(%s)", (key,)).fetchone()
    assert row == (False,)


def test_resolve_tenant_by_slug_returns_ids_and_flags_only(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    maint, api = connect("pickwise_maint"), connect("pickwise_api")
    with as_ops(maint):
        slug = maint.execute(
            "SELECT slug FROM platform.tenants WHERE id = %s", (a.tenant_id,)
        ).fetchone()
    assert slug is not None
    cols = api.execute("SELECT * FROM platform.resolve_tenant_by_slug(%s)", (str(slug[0]),))
    assert [d.name for d in cols.description or []] == ["tenant_id", "status", "careers_enabled"]
    assert cols.fetchone() == (a.tenant_id, "active", False)
    assert (
        api.execute("SELECT * FROM platform.resolve_tenant_by_slug('no-such-slug')").fetchone()
        is None
    )


def test_resolve_sso_by_domain_lists_active_tenants_only(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    maint, api = connect("pickwise_maint"), connect("pickwise_api")
    domain = f"sso-{uuid.uuid4().hex[:8]}.test"
    with as_ops(maint):
        for tenant, enforce in ((a, True), (b, False)):
            maint.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant.tenant_id),))
            maint.execute(
                "INSERT INTO platform.tenant_sso_configs "
                "(issuer, client_id, client_secret_enc, allowed_domains, enforce_sso) "
                "VALUES ('https://idp.example.test', 'c', '\\x00', ARRAY[%s]::citext[], %s)",
                (domain, enforce),
            )
    sql = "SELECT tenant_id, enforce_sso FROM platform.resolve_sso_by_domain(%s)"
    rows = api.execute(sql, (domain,)).fetchall()
    assert {(r[0], r[1]) for r in rows} == {(a.tenant_id, True), (b.tenant_id, False)}
    with as_ops(maint):
        maint.execute(
            "UPDATE platform.tenants SET status = 'suspended' WHERE id = %s", (b.tenant_id,)
        )
    assert [r[0] for r in api.execute(sql, (domain,)).fetchall()] == [a.tenant_id]
    assert api.execute(sql, ("other.test",)).fetchall() == []
    assert api.execute("SELECT count(*) FROM platform.tenant_sso_configs").fetchone() == (0,)


def test_the_new_global_tables_are_not_readable_across_roles_unexpectedly(connect: Connect) -> None:
    api = connect("pickwise_api")
    # The app may spend rate-limit tokens, and queue pre-tenant email, but never edit the outbox.
    api.execute("SELECT 1 FROM platform.rate_limit_buckets LIMIT 1")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("UPDATE platform.platform_email_outbox SET status = 'sent'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("DELETE FROM platform.platform_email_outbox")
