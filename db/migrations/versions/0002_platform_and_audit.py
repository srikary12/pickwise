# SPDX-License-Identifier: AGPL-3.0-only
"""platform + audit tables (DATA_MODEL §1-2).

Creates every table, the first audit.events partitions, the global-table grants
and the pre-tenant SECURITY DEFINER functions (db/migrations/sql/0002_*.sql), then
applies PII comments from db/pii_classification.yaml, tenant RLS policies, and
the audit trigger on the audited platform tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
from helpers import apply_pii_comments, apply_tenant_policies, attach_audit, execute_sql_file

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDITED = (
    "platform.memberships",
    "platform.roles",
    "platform.role_permissions",
    "platform.role_assignments",
)


def upgrade() -> None:
    execute_sql_file("0002_platform_and_audit.sql")
    apply_pii_comments(("platform", "audit"))
    apply_tenant_policies("platform")
    apply_tenant_policies("audit")
    for table in AUDITED:
        attach_audit(table)


def downgrade() -> None:
    op.execute("DROP FUNCTION audit.log_platform_event(text, uuid, text, inet)")
    op.execute("DROP FUNCTION platform.resolve_invite(bytea)")
    op.execute("DROP FUNCTION platform.list_memberships_for_user(uuid)")
    op.execute("DROP TABLE audit.events")
    # Children before parents; every FK here is inside these two schemas.
    op.execute(
        """
        DO $$
        DECLARE t text;
        BEGIN
            FOR t IN
                SELECT format('%I.%I', n.nspname, c.relname) FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'platform' AND c.relkind = 'r'
            LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || t || ' CASCADE';
            END LOOP;
        END
        $$
        """
    )
