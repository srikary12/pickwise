# SPDX-License-Identifier: AGPL-3.0-only
"""System roles seeded into every tenant at provisioning (DATA_MODEL §1 roles).

Their permissions come from each Permission's ``default_roles``, so a module that
adds permissions later also extends the system roles; ``pickwise tenant
sync-defaults`` backfills existing tenants.
"""

from dataclasses import dataclass

from pickwise.platform.permissions import PERMISSIONS


@dataclass(frozen=True, slots=True)
class SystemRole:
    key: str
    name: str
    description: str


SYSTEM_ROLES: tuple[SystemRole, ...] = (
    SystemRole("tenant_admin", "Tenant admin", "Full administrative access to the tenant"),
    SystemRole("hr_admin", "HR admin", "Manages people data and HR configuration"),
    SystemRole("hr_ops", "HR operations", "Day-to-day HR operations"),
    SystemRole("payroll_admin", "Payroll admin", "Runs payroll and manages compensation"),
    SystemRole("recruiter", "Recruiter", "Manages jobs, candidates and pipelines"),
    SystemRole("hiring_manager", "Hiring manager", "Reviews candidates for their own jobs"),
    SystemRole("interviewer", "Interviewer", "Gives interview feedback"),
    SystemRole("manager", "Manager", "Manages their team (scoped to their reports)"),
    SystemRole("employee", "Employee", "Self-service access to their own records"),
)


def default_grants(role_key: str) -> tuple[str, ...]:
    return tuple(p.code for p in PERMISSIONS.all() if role_key in p.default_roles)
