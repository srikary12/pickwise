# SPDX-License-Identifier: AGPL-3.0-only
"""Approval task deadlines and reminders, and the erasure purge-mode functions.

See db/migrations/sql/0005_approvals_erasure.sql and ADRs 0016 and 0018.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op
from helpers import apply_pii_comments, execute_sql_file

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    execute_sql_file("0005_approvals_erasure.sql")
    apply_pii_comments(("platform",))


def downgrade() -> None:
    op.execute("DROP FUNCTION platform.end_erasure()")
    op.execute("DROP FUNCTION platform.begin_erasure()")
    op.execute("DROP INDEX platform.approval_tasks_due")
    op.execute("ALTER TABLE platform.approval_requests DROP COLUMN attributes")
    op.execute("ALTER TABLE platform.approval_tasks DROP COLUMN reminded_at")
    op.execute("ALTER TABLE platform.approval_tasks DROP COLUMN due_at")
