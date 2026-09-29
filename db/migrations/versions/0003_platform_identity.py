# SPDX-License-Identifier: AGPL-3.0-only
"""Identity and access: rate-limit buckets, pre-tenant email outbox, encrypted
email payloads, TOTP replay protection, and the pre-tenant lookups
resolve_api_key / resolve_tenant_by_slug / resolve_sso_by_domain.

See db/migrations/sql/0003_platform_identity.sql and ADRs 0004, 0010-0012.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op
from helpers import apply_pii_comments, execute_sql_file

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    execute_sql_file("0003_platform_identity.sql")
    apply_pii_comments(("platform",))


def downgrade() -> None:
    op.execute("DROP FUNCTION platform.resolve_sso_by_domain(citext)")
    op.execute("DROP FUNCTION platform.resolve_tenant_by_slug(citext)")
    op.execute("DROP FUNCTION platform.resolve_api_key(bytea)")
    op.execute("DROP TABLE platform.rate_limit_buckets")
    op.execute("DROP TABLE platform.platform_email_outbox")
    op.execute("ALTER TABLE platform.email_outbox DROP COLUMN payload_enc")
    op.execute("ALTER TABLE platform.signup_requests DROP COLUMN requested_name")
    op.execute("ALTER TABLE platform.sessions DROP COLUMN sso_tenant_id")
    op.execute("ALTER TABLE platform.users DROP COLUMN mfa_last_used_step")
