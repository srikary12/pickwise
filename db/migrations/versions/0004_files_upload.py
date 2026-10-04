# SPDX-License-Identifier: AGPL-3.0-only
"""Upload bookkeeping for the file pipeline: declared type and size, upload
expiry, completion time and the scan detail.

See db/migrations/sql/0004_files_upload.sql and ADR 0013.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op
from helpers import apply_pii_comments, execute_sql_file

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    execute_sql_file("0004_files_upload.sql")
    apply_pii_comments(("platform",))


def downgrade() -> None:
    op.execute("DROP INDEX platform.files_retention")
    op.execute("DROP INDEX platform.files_pending_expiry")
    op.execute("ALTER TABLE platform.files DROP COLUMN scan_detail")
    op.execute("ALTER TABLE platform.files DROP COLUMN uploaded_at")
    op.execute("ALTER TABLE platform.files DROP COLUMN upload_expires_at")
    op.execute("ALTER TABLE platform.files DROP COLUMN declared_size_bytes")
    op.execute("ALTER TABLE platform.files DROP COLUMN declared_mime_type")
