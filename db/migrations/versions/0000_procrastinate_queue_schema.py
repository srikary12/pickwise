# SPDX-License-Identifier: AGPL-3.0-only
"""procrastinate job-queue objects in their own `queue` schema.

procrastinate is the Postgres-backed background-job library. Its schema SQL is
vendored (db/migrations/vendor/procrastinate-3.10.0-schema.sql) rather than read
from the installed package, so this revision stays reproducible when the library
is upgraded; upgrades get their own revisions wrapping procrastinate's migration SQL.

The queue tables are global (no tenant_id, no RLS), so job arguments must carry
only ids and tenant_id (CLAUDE.md rule 14).

Revision ID: 0000
Revises:
Create Date: 2026-09-28
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0000"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "vendor" / "procrastinate-3.10.0-schema.sql"


def upgrade() -> None:
    op.execute("CREATE SCHEMA queue AUTHORIZATION pickwise_owner")
    op.execute("SET LOCAL search_path = queue")
    # Run the multi-statement script on the raw psycopg connection with no
    # parameters, so psycopg uses the simple query protocol and leaves '%' alone.
    raw = op.get_bind().connection.driver_connection
    assert raw is not None
    with raw.cursor() as cur:
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    op.execute("RESET search_path")
    # procrastinate's functions and triggers use unqualified names. Pin their
    # search_path so they work (and can't be hijacked) whatever the caller's
    # search_path is, e.g. an ops purge or psql session.
    op.execute(
        """
        DO $$
        DECLARE fn regprocedure;
        BEGIN
            FOR fn IN
                SELECT p.oid::regprocedure FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'queue'
            LOOP
                EXECUTE format('ALTER ROUTINE %s SET search_path = queue, pg_catalog', fn);
            END LOOP;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON SCHEMA queue FROM PUBLIC")
    for role in ("pickwise_app", "pickwise_ops"):
        op.execute(f"GRANT USAGE ON SCHEMA queue TO {role}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA queue TO {role}")
        op.execute(f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA queue TO {role}")
        op.execute(f"GRANT EXECUTE ON ALL ROUTINES IN SCHEMA queue TO {role}")


def downgrade() -> None:
    op.execute("DROP SCHEMA queue CASCADE")
