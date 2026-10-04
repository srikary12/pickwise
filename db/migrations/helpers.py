# SPDX-License-Identifier: AGPL-3.0-only
"""Helpers for hand-written migrations.

The RLS, grant, append-only and audit rules live in SQL functions created by
revision 0001 (platform.apply_tenant_policies, platform.make_append_only,
audit.attach, …), so every module migration and the partition function share one
definition. These wrappers keep migration files short and readable.
"""

from pathlib import Path

from alembic import op

from pickwise.shared import pii

SQL_DIR = Path(__file__).resolve().parent / "sql"


def execute_sql_file(name: str) -> None:
    """Run a multi-statement .sql file from db/migrations/sql.

    The raw psycopg cursor with no parameters uses the simple query protocol, so
    the script may contain many statements and literal '%' characters.
    """
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    raw = op.get_bind().connection.driver_connection
    if raw is None:
        raise RuntimeError("no DBAPI connection available")
    with raw.cursor() as cur:
        cur.execute(sql)


def apply_tenant_policies(schema: str) -> None:
    """RLS + the three policies, grants and touch_row for every tenant table in a schema."""
    op.execute(f"SELECT platform.apply_tenant_policies('{_ident(schema)}')")


def make_append_only(table: str) -> None:
    op.execute(f"SELECT platform.make_append_only('{_qualified(table)}')")


def attach_audit(table: str) -> None:
    """Audit trigger; redacts every '@pii' column, so apply PII comments first."""
    op.execute(f"SELECT audit.attach('{_qualified(table)}')")


def apply_pii_comments(schemas: tuple[str, ...]) -> int:
    """Write '@pii …' comments from db/pii_classification.yaml for existing columns.

    The YAML describes the current schema while each migration runs against the
    schema as of its revision, so columns that don't exist yet are skipped; the
    migration that adds them applies their comments.
    """
    bind = op.get_bind()
    written = 0
    for col in pii.personal_columns(pii.load()):
        if col.schema not in schemas:
            continue
        exists = bind.exec_driver_sql(
            "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(%(t)s) "
            "AND attname = %(c)s AND NOT attisdropped)",
            {"t": col.qualified_table, "c": col.column},
        ).scalar()
        if not exists:
            continue
        op.execute(
            f'COMMENT ON COLUMN {_qualified(col.qualified_table)}."{_ident(col.column)}" '
            f"IS '{col.comment}'"
        )
        written += 1
    return written


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum() or not name[0].isalpha():
        raise ValueError(f"unsafe identifier {name!r}")
    return name


def _qualified(name: str) -> str:
    schema, _, table = name.partition(".")
    return f"{_ident(schema)}.{_ident(table)}"
