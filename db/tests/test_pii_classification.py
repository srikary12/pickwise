# SPDX-License-Identifier: AGPL-3.0-only
"""db/pii_classification.yaml and the database's '@pii' comments agree (ADR 0005)."""

import pytest
from conftest import Connect

from pickwise.shared import pii

pytestmark = pytest.mark.db


def test_yaml_and_column_comments_match_in_both_directions(connect: Connect) -> None:
    conn = connect("pickwise_api")
    existing_tables = {
        str(r[0])
        for r in conn.execute(
            "SELECT n.nspname || '.' || c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition"
        ).fetchall()
    }
    expected = {
        (c.qualified_table, c.column, c.comment)
        for c in pii.personal_columns(pii.load())
        if c.qualified_table in existing_tables
    }
    actual = {
        (str(r[0]), str(r[1]), str(r[2]))
        for r in conn.execute(
            "SELECT n.nspname || '.' || c.relname, a.attname, col_description(c.oid, a.attnum) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
            "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition "
            "AND col_description(c.oid, a.attnum) LIKE '@pii %'"
        ).fetchall()
    }
    assert expected - actual == set(), "classified in YAML but no comment in the database"
    assert actual - expected == set(), "'@pii' comment in the database but not in the YAML"
    assert len(actual) >= 30  # platform + audit alone classify 30 columns


def test_audited_tables_redact_exactly_their_personal_columns(connect: Connect) -> None:
    conn = connect("pickwise_api")
    rows = conn.execute(
        "SELECT tgrelid::regclass::text, tgnargs FROM pg_trigger WHERE tgname = 'audit_capture'"
    ).fetchall()
    audited = {str(r[0]) for r in rows}
    assert {
        "platform.memberships",
        "platform.roles",
        "platform.role_permissions",
        "platform.role_assignments",
    } <= audited
