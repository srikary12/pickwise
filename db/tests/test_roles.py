# SPDX-License-Identifier: AGPL-3.0-only
"""The database role model (CLAUDE.md rule 4): no BYPASSRLS anywhere, and the ops
role is reachable only through an explicit SET ROLE by the worker and maint users."""

from collections.abc import Callable

import psycopg
import pytest

from pickwise.cli.db import verify_role_model

Connect = Callable[[str], psycopg.Connection[tuple[object, ...]]]

pytestmark = pytest.mark.db


def test_role_model_matches_claude_md(connect: Connect) -> None:
    verify_role_model(connect("pickwise_api"))


def test_no_role_has_bypassrls_or_superuser(connect: Connect) -> None:
    rows = (
        connect("pickwise_api")
        .execute(
            "SELECT rolname FROM pg_roles "
            "WHERE rolname LIKE 'pickwise\\_%' AND (rolbypassrls OR rolsuper)"
        )
        .fetchall()
    )
    assert rows == []


def test_migrator_acts_as_owner(connect: Connect) -> None:
    row = connect("pickwise_migrator").execute("SELECT current_user").fetchone()
    assert row == ("pickwise_owner",)


@pytest.mark.parametrize("user", ["pickwise_worker", "pickwise_maint"])
def test_ops_capable_users_start_without_ops_privileges(connect: Connect, user: str) -> None:
    conn = connect(user)
    row = conn.execute(
        "SELECT current_user, pg_has_role(current_user, 'pickwise_ops', 'USAGE'), "
        "pg_has_role(current_user, 'pickwise_ops', 'SET')"
    ).fetchone()
    # INHERIT FALSE: no privileges of pickwise_ops until an explicit SET ROLE.
    assert row == (user, False, True)
    with conn.transaction():
        conn.execute("SET LOCAL ROLE pickwise_ops")
        assert conn.execute("SELECT current_user").fetchone() == ("pickwise_ops",)
    assert conn.execute("SELECT current_user").fetchone() == (user,)


def test_api_cannot_become_ops(connect: Connect) -> None:
    conn = connect("pickwise_api")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SET ROLE pickwise_ops")


@pytest.mark.parametrize("user", ["pickwise_api", "pickwise_worker", "pickwise_maint"])
def test_app_users_cannot_create_objects(connect: Connect, user: str) -> None:
    conn = connect(user)
    for schema in ("public", "queue"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(f"CREATE TABLE {schema}.should_not_exist (id int)")


def test_queue_schema_is_owned_by_owner(connect: Connect) -> None:
    row = (
        connect("pickwise_api")
        .execute("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'queue'")
        .fetchone()
    )
    assert row == ("pickwise_owner",)


def test_extensions_installed(connect: Connect) -> None:
    rows = connect("pickwise_api").execute("SELECT extname FROM pg_extension").fetchall()
    assert {"pgcrypto", "citext", "btree_gist", "pg_trgm", "ltree"} <= {r[0] for r in rows}


def test_uuidv7_is_native(connect: Connect) -> None:
    row = connect("pickwise_api").execute("SELECT uuid_extract_version(uuidv7())").fetchone()
    assert row == (7,)


def test_queue_routines_pin_their_search_path(connect: Connect) -> None:
    rows = (
        connect("pickwise_api")
        .execute(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'queue' AND NOT coalesce("
            "'search_path=queue, pg_catalog' = ANY(p.proconfig), false)"
        )
        .fetchall()
    )
    assert rows == []
