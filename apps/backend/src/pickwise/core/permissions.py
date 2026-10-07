# SPDX-License-Identifier: AGPL-3.0-only
"""Core HR permissions. Registered when ``wiring.register_all`` imports this module.

Who holds what by default:
- HR roles hold the broad permissions at tenant scope.
- A manager's reach comes from the role assignment's scope (direct or all reports); the
  ``manager`` system role is assigned automatically when someone gets a direct report.
- Everyone holds the self-service permissions (``core.me.*``) and the directory, which shows
  only work details of active colleagues.
- Personal, identity and bank data each need their own permission, and revealing a restricted
  value needs a further one; every reveal is audited.
"""

from pickwise.platform.permissions import (
    EVERY_ROLE,
    HR_ADMIN,
    PERMISSIONS,
    TENANT_ADMIN,
    Permission,
)

HR = (TENANT_ADMIN, HR_ADMIN, "hr_ops")
HR_FULL = (TENANT_ADMIN, HR_ADMIN)

PERMISSIONS.register(
    Permission(
        "core.org.read",
        "core",
        "View legal entities, locations, departments, designations and grades",
        default_roles=EVERY_ROLE,
    ),
    Permission(
        "core.org.manage",
        "core",
        "Create and change the organisation structure",
        default_roles=(TENANT_ADMIN, HR_ADMIN),
    ),
    Permission(
        "core.directory.read",
        "core",
        "Find colleagues and see their work details and the org chart",
        default_roles=EVERY_ROLE,
    ),
    Permission(
        "core.employees.read",
        "core",
        "View employee records: status, dates, job, education, experience",
        default_roles=(*HR, "manager"),
    ),
    Permission(
        "core.employees.create",
        "core",
        "Add employees",
        default_roles=HR,
    ),
    Permission(
        "core.employees.update",
        "core",
        "Change employee records and move them through onboarding statuses",
        default_roles=HR,
    ),
    Permission(
        "core.jobrecords.read",
        "core",
        "View an employee's job history",
        default_roles=(*HR, "manager"),
    ),
    Permission(
        "core.jobrecords.change",
        "core",
        "Promote, transfer, change manager or correct a job record",
        default_roles=HR_FULL,
    ),
    Permission(
        "core.employee.personal.read",
        "core",
        "View personal details, addresses, emergency contacts, dependents and nominations",
        is_sensitive=True,
        default_roles=HR_FULL,
    ),
    Permission(
        "core.employee.personal.update",
        "core",
        "Change personal details, addresses, emergency contacts, dependents and nominations",
        is_sensitive=True,
        default_roles=HR_FULL,
    ),
    Permission(
        "core.employee.identity.read",
        "core",
        "View identity documents (values stay masked)",
        is_sensitive=True,
        default_roles=HR_FULL,
    ),
    Permission(
        "core.employee.identity.update",
        "core",
        "Add, replace and verify identity documents",
        is_sensitive=True,
        default_roles=HR_FULL,
    ),
    Permission(
        "core.employee.identity.reveal",
        "core",
        "Reveal a full identity number (audited)",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN,),
    ),
    Permission(
        "core.employee.bank.read",
        "core",
        "View bank accounts (account numbers stay masked)",
        is_sensitive=True,
        default_roles=(*HR_FULL, "payroll_admin"),
    ),
    Permission(
        "core.employee.bank.update",
        "core",
        "Add and change bank accounts",
        is_sensitive=True,
        default_roles=(*HR_FULL, "payroll_admin"),
    ),
    Permission(
        "core.employee.bank.reveal",
        "core",
        "Reveal a full bank account number (audited)",
        is_sensitive=True,
        default_roles=(TENANT_ADMIN, "payroll_admin"),
    ),
    Permission(
        "core.me.read",
        "core",
        "View your own employee record",
        default_roles=EVERY_ROLE,
    ),
    Permission(
        "core.me.update",
        "core",
        "Change your own contact details, address and emergency contacts",
        default_roles=EVERY_ROLE,
    ),
)
