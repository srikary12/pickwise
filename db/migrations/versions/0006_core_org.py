# SPDX-License-Identifier: AGPL-3.0-only
"""core organisation structure: legal entities, locations, cost centres, designations,
grades and the department tree (DATA_MODEL §3).

See db/migrations/sql/0006_core_org.sql and ADR 0021.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op
from helpers import apply_pii_comments, apply_tenant_policies, attach_audit, execute_sql_file

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "legal_entities",
    "legal_entity_registrations",
    "locations",
    "cost_centers",
    "designations",
    "grades",
    "departments",
)


def upgrade() -> None:
    execute_sql_file("0006_core_org.sql")
    apply_pii_comments(("core",))
    apply_tenant_policies("core")
    for table in TABLES:
        attach_audit(f"core.{table}")


def downgrade() -> None:
    op.execute("DROP TABLE core.departments")
    op.execute("DROP FUNCTION core.department_path_after()")
    op.execute("DROP FUNCTION core.department_path_before()")
    for table in reversed(TABLES[:-1]):
        op.execute(f"DROP TABLE core.{table}")
