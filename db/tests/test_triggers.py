# SPDX-License-Identifier: AGPL-3.0-only
"""touch_row, append-only enforcement and the audit trigger."""

import json

import psycopg
import pytest
from conftest import Connect, SeededTenant, as_ops, as_tenant

pytestmark = pytest.mark.db


def test_touch_row_bumps_version_and_sets_updated_fields(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        before = api.execute(
            "SELECT row_version, updated_at FROM platform.roles WHERE id = %s", (a.role_id,)
        ).fetchone()
    with as_tenant(api, a.tenant_id, a.user_id):
        api.execute("UPDATE platform.roles SET name = 'Admins' WHERE id = %s", (a.role_id,))
        after = api.execute(
            "SELECT row_version, updated_at, updated_by FROM platform.roles WHERE id = %s",
            (a.role_id,),
        ).fetchone()
    assert before is not None
    assert after is not None
    assert after[0] == int(str(before[0])) + 1
    assert after[1] != before[1]
    assert after[2] == a.user_id


@pytest.mark.parametrize(
    "assignment",
    [
        "tenant_id = gen_random_uuid()",
        "created_at = now() - interval '1 day'",
        "created_by = gen_random_uuid()",
    ],
)
def test_touch_row_freezes_identity_columns(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant], assignment: str
) -> None:
    a, _ = two_tenants
    maint = connect("pickwise_maint")
    # As ops, so RLS WITH CHECK can't be what stops the tenant_id change.
    with (
        pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="immutable"),
        as_ops(maint),
    ):
        maint.execute(f"UPDATE platform.roles SET {assignment} WHERE id = %s", (a.role_id,))


def test_audit_events_are_append_only_for_the_app(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_tenant(api, a.tenant_id):
        api.execute("UPDATE audit.events SET action = 'x'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_tenant(api, a.tenant_id):
        api.execute("DELETE FROM audit.events")


def test_forbid_mutation_blocks_ops_outside_purge_mode(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    maint = connect("pickwise_maint")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"), as_ops(maint):
        maint.execute(
            "UPDATE audit.events SET action = 'tampered' WHERE tenant_id = %s", (a.tenant_id,)
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"), as_ops(maint):
        maint.execute("DELETE FROM audit.events WHERE tenant_id = %s", (a.tenant_id,))


def test_purge_mode_allows_delete_and_audit_update_for_ops_only(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    maint = connect("pickwise_maint")
    with as_ops(maint, purge_mode=True):
        updated = maint.execute(
            "UPDATE audit.events SET changes = NULL WHERE tenant_id = %s", (a.tenant_id,)
        ).rowcount
        deleted = maint.execute(
            "DELETE FROM audit.events WHERE tenant_id = %s", (a.tenant_id,)
        ).rowcount
    assert updated > 0
    assert deleted == updated
    # Purge mode means nothing to the app role: it has no DELETE privilege at all.
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id):
        api.execute("SELECT set_config('app.purge_mode', 'on', true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute("DELETE FROM audit.events")


def test_audit_trigger_records_actor_request_and_a_diff(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id, request_id="req-audit-1"):
        api.execute(
            "UPDATE platform.roles SET name = 'Renamed', description = 'd' WHERE id = %s",
            (a.role_id,),
        )
        row = api.execute(
            "SELECT actor_type, actor_user_id, action, entity_id, request_id, host(ip), changes "
            "FROM audit.events WHERE entity_table = 'roles' AND request_id = 'req-audit-1'"
        ).fetchone()
    assert row is not None
    actor_type, actor, action, entity_id, request_id, ip, changes = row
    assert (actor_type, actor, action, entity_id, request_id, ip) == (
        "user",
        a.user_id,
        "update",
        a.role_id,
        "req-audit-1",
        "203.0.113.7",
    )
    assert changes == {"name": ["Tenant admin", "Renamed"], "description": [None, "d"]}


def test_audit_redacts_confidential_and_restricted_columns(connect: Connect) -> None:
    """audit.attach() reads '@pii' comments; exercise it on a scratch table as the owner."""
    migrator = connect("pickwise_migrator")
    # An explicit transaction that is always rolled back: nothing is left behind.
    migrator.execute("BEGIN")
    try:
        migrator.execute(
            "CREATE TABLE platform.audit_probe (tenant_id uuid NOT NULL, id uuid NOT NULL DEFAULT uuidv7(), "
            "note text, secret text, email text, PRIMARY KEY (tenant_id, id))"
        )
        migrator.execute("COMMENT ON COLUMN platform.audit_probe.secret IS '@pii restricted'")
        migrator.execute("COMMENT ON COLUMN platform.audit_probe.email IS '@pii confidential'")
        redacted = migrator.execute("SELECT audit.attach('platform.audit_probe')").fetchone()
        tenant = migrator.execute(
            "INSERT INTO platform.tenants (slug, name) VALUES ('audit-probe', 'Probe') RETURNING id"
        ).fetchone()
        assert tenant is not None
        migrator.execute(
            "INSERT INTO platform.audit_probe (tenant_id, note, secret, email) "
            "VALUES (%s, 'hello', 'hunter2', 'a@b.c')",
            (tenant[0],),
        )
        migrator.execute(
            "UPDATE platform.audit_probe SET note = 'bye', secret = 's3cret', email = 'x@y.z'"
        )
        rows = migrator.execute(
            "SELECT action, changes FROM audit.events WHERE entity_table = 'audit_probe' ORDER BY occurred_at, id"
        ).fetchall()
        dumped = json.dumps([r[1] for r in rows])
    finally:
        migrator.execute("ROLLBACK")
    assert redacted == (["secret", "email"],)
    assert [r[0] for r in rows] == ["insert", "update"]
    assert rows[0][1] == {"note": [None, "hello"], "secret": "[set]", "email": "[set]"}
    assert rows[1][1] == {"note": ["hello", "bye"], "secret": "[changed]", "email": "[changed]"}
    for value in ("hunter2", "s3cret", "a@b.c", "x@y.z"):
        assert value not in dumped
