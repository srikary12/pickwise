# SPDX-License-Identifier: AGPL-3.0-only
"""Foundation: module schemas, context functions, row triggers, RLS/grant helpers,
partitions, audit capture and the ops purge/scrub functions.

See db/migrations/sql/0001_foundation.sql, DATA_MODEL §0 and ADRs 0002/0003.

Revision ID: 0001
Revises: 0000
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
from helpers import execute_sql_file

revision: str = "0001"
down_revision: str | None = "0000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ("recruit", "ai", "payroll", "attendance", "leave", "core", "audit", "platform")


def upgrade() -> None:
    execute_sql_file("0001_foundation.sql")


def downgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"DROP SCHEMA {schema} CASCADE")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE pickwise_owner GRANT EXECUTE ON ROUTINES TO PUBLIC"
    )
