# SPDX-License-Identifier: AGPL-3.0-only
"""Catalog-driven isolation checks (CLAUDE.md rules 1 and 5, ADRs 0002/0003).

The list of tables comes from pg_catalog, so a new table without its policies,
or a partition with app grants, fails CI without anyone adding a test.
"""

import psycopg
import pytest
from conftest import Connect

pytestmark = pytest.mark.db

SCHEMAS = ("platform", "audit", "core", "leave", "attendance", "payroll", "ai", "recruit")

# DATA_MODEL §0 "Global (non-tenant) tables". Anything else with tenant_id is a tenant table.
EXPECTED_GLOBAL = {
    "platform.users",
    "platform.user_identities",
    "platform.sessions",
    "platform.permissions",
    "platform.auth_tokens",
    "platform.mfa_recovery_codes",
    "platform.platform_keys",
    "platform.signup_requests",
    "platform.platform_email_outbox",
    "platform.rate_limit_buckets",
}

EXPECTED_POLICIES = {
    (
        "tenant_isolation",
        "{public}",
        "(tenant_id = platform.current_tenant_id())",
        "(tenant_id = platform.current_tenant_id())",
    ),
    ("ops_all", "{pickwise_ops}", "true", "true"),
    ("owner_all", "{pickwise_owner}", "true", "true"),
}

_TENANT_TABLES = """
    SELECT c.oid::regclass::text, c.relispartition, c.relrowsecurity, c.relforcerowsecurity,
           coalesce(obj_description(c.oid, 'pg_class'), '')
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p')
      AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                  AND a.attname = 'tenant_id' AND NOT a.attisdropped)
    ORDER BY 1
"""


def _tables(conn: psycopg.Connection[tuple[object, ...]]) -> list[tuple[object, ...]]:
    return conn.execute(_TENANT_TABLES, (list(SCHEMAS),)).fetchall()


def test_global_tables_are_exactly_the_documented_ones(connect: Connect) -> None:
    conn = connect("pickwise_api")
    marked = {
        str(r[0])
        for r in conn.execute(
            "SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = ANY(%s) AND obj_description(c.oid, 'pg_class') ~ '(^|\\s)@global(\\s|$)'",
            (list(SCHEMAS),),
        ).fetchall()
    }
    assert marked == EXPECTED_GLOBAL


def test_every_tenant_table_and_partition_has_rls_and_exactly_three_policies(
    connect: Connect,
) -> None:
    conn = connect("pickwise_api")
    tables = [t for t in _tables(conn) if "@global" not in str(t[4])]
    assert len(tables) > 25, "expected the platform tenant tables and audit partitions"
    problems = []
    for name, _is_partition, rls, forced, _comment in tables:
        if not (rls and forced):
            problems.append(f"{name}: RLS enabled={rls} forced={forced}")
        policies = {
            (str(p[0]), str(p[1]), str(p[2]), str(p[3]))
            for p in conn.execute(
                "SELECT policyname, roles::text, qual, with_check FROM pg_policies "
                "WHERE format('%%I.%%I', schemaname, tablename) = %s",
                (name,),
            ).fetchall()
        }
        if policies != EXPECTED_POLICIES:
            problems.append(f"{name}: policies {sorted(policies)}")
    assert not problems, "\n".join(problems)


def test_tenants_table_is_isolated_on_its_own_id(connect: Connect) -> None:
    conn = connect("pickwise_api")
    row = conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = 'platform.tenants'::regclass"
    ).fetchone()
    assert row == (True, True)
    qual = conn.execute(
        "SELECT qual FROM pg_policies WHERE schemaname = 'platform' AND tablename = 'tenants' "
        "AND policyname = 'tenant_isolation'"
    ).fetchone()
    assert qual == ("(id = platform.current_tenant_id())",)


def test_app_has_no_privileges_on_partitions(connect: Connect) -> None:
    conn = connect("pickwise_api")
    partitions = [str(t[0]) for t in _tables(conn) if t[1]]
    assert partitions, "audit.events should have partitions"
    leaking = [
        p
        for p in partitions
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE")
        if _has_priv(conn, "pickwise_app", p, priv)
    ]
    assert leaking == []


def test_append_only_tables_grant_the_app_only_select_and_insert(connect: Connect) -> None:
    conn = connect("pickwise_api")
    appendonly = [str(t[0]) for t in _tables(conn) if "@append_only" in str(t[4]) and not t[1]]
    assert "audit.events" in appendonly
    for table in appendonly:
        assert _has_priv(conn, "pickwise_app", table, "SELECT")
        assert _has_priv(conn, "pickwise_app", table, "INSERT")
        assert not _has_priv(conn, "pickwise_app", table, "UPDATE"), table
        assert not _has_priv(conn, "pickwise_app", table, "DELETE"), table


def _has_priv(
    conn: psycopg.Connection[tuple[object, ...]], role: str, table: str, priv: str
) -> bool:
    row = conn.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, priv)).fetchone()
    return bool(row and row[0])


def test_fk_graph_has_no_cycles_beyond_self_references(connect: Connect) -> None:
    conn = connect("pickwise_api")
    edges = conn.execute(
        "SELECT k.conrelid::regclass::text, k.confrelid::regclass::text FROM pg_constraint k "
        "JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE k.contype = 'f' AND NOT c.relispartition AND n.nspname = ANY(%s)",
        (list(SCHEMAS),),
    ).fetchall()
    graph: dict[str, set[str]] = {}
    for child, parent in edges:
        if child != parent:  # self-references are allowed (purge nulls them first)
            graph.setdefault(str(child), set()).add(str(parent))

    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in done:
            return
        if node in visiting:
            raise AssertionError("FK cycle: " + " -> ".join([*path, node]))
        visiting.add(node)
        for nxt in graph.get(node, ()):
            visit(nxt, [*path, node])
        visiting.discard(node)
        done.add(node)

    for node in list(graph):
        visit(node, [])


def test_fks_between_tenant_tables_include_tenant_id(connect: Connect) -> None:
    conn = connect("pickwise_api")
    rows = conn.execute(
        """
        SELECT k.conname, k.conrelid::regclass::text,
               (SELECT array_agg(a.attname::text) FROM pg_attribute a
                WHERE a.attrelid = k.conrelid AND a.attnum = ANY(k.conkey))
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid AND NOT c.relispartition
        JOIN pg_class p ON p.oid = k.confrelid
        WHERE k.contype = 'f'
          AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.conrelid AND a.attname = 'tenant_id')
          AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.confrelid AND a.attname = 'tenant_id')
          AND coalesce(obj_description(p.oid, 'pg_class'), '') !~ '@global'
          AND coalesce(obj_description(c.oid, 'pg_class'), '') !~ '@global'
        """
    ).fetchall()
    assert rows, "expected composite FKs between tenant tables"
    single = [f"{r[1]}.{r[0]} {r[2]}" for r in rows if "tenant_id" not in list(r[2] or [])]  # type: ignore[call-overload]
    assert single == []
