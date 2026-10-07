# SPDX-License-Identifier: AGPL-3.0-only
"""core employees: master data, personal data, identity documents, bank accounts,
effective-dated job records and the reporting hierarchy (DATA_MODEL §3).

See db/migrations/sql/0007_core_employees.sql and ADRs 0022 and 0023.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op
from helpers import apply_pii_comments, apply_tenant_policies, attach_audit, execute_sql_file

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDITED = (
    "employees",
    "employee_personal",
    "employee_addresses",
    "emergency_contacts",
    "dependents",
    "nominations",
    "identity_documents",
    "bank_accounts",
    "employee_job_records",
    "employee_documents",
    "education_history",
    "employment_history",
)
# Children before parents.
DROP_ORDER = (
    "employment_history",
    "education_history",
    "employee_documents",
    "employee_hierarchy",
    "employee_job_records",
    "bank_accounts",
    "identity_documents",
    "nominations",
    "dependents",
    "emergency_contacts",
    "employee_addresses",
    "employee_personal",
)


def upgrade() -> None:
    execute_sql_file("0007_core_employees.sql")
    apply_pii_comments(("core",))
    apply_tenant_policies("core")
    for table in AUDITED:
        attach_audit(f"core.{table}")


def downgrade() -> None:
    op.execute("DROP VIEW core.employee_job_records_current")
    op.execute(
        "ALTER TABLE core.departments DROP CONSTRAINT departments_tenant_id_head_employee_id_fkey"
    )
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE core.{table}")
    op.execute("DROP TABLE core.employees")
    op.execute("DROP FUNCTION core.check_nomination_shares()")
    op.execute("DROP FUNCTION core.today()")
