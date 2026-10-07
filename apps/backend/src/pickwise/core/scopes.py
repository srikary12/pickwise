# SPDX-License-Identifier: AGPL-3.0-only
"""Core's data-scope resolvers (CLAUDE.md rule 16). Registered into platform's registry.

A resolver turns one granted scope into a SQL condition on the employee column of the table
being queried (``ScopeTarget.employee_column``). The org scopes read the *current* job records
and the reporting closure table; ``self`` finds the caller's own employee through their
membership. A scope without the column to filter on denies.
"""

import uuid

from sqlalchemy import ColumnElement, false, select

from pickwise.core.tables import current_jobs, departments, employees, hierarchy
from pickwise.platform.rbac.principal import Principal
from pickwise.platform.scopes import SCOPE_RESOLVERS, DataScope, ScopeTarget, ScopeType


def my_employee_id(principal: Principal) -> ColumnElement[uuid.UUID]:
    """The caller's own employee row, as a scalar subquery (NULL if they have none)."""
    return (
        select(employees.c.id).where(employees.c.membership_id == principal.membership_id)
    ).scalar_subquery()


def _by_job_column(name: str, scope: DataScope, target: ScopeTarget) -> ColumnElement[bool]:
    if target.employee_column is None or scope.scope_id is None:
        return false()
    return target.employee_column.in_(
        select(current_jobs.c.employee_id).where(current_jobs.c[name] == scope.scope_id)
    )


def _legal_entity(scope: DataScope, _p: Principal, target: ScopeTarget) -> ColumnElement[bool]:
    return _by_job_column("legal_entity_id", scope, target)


def _location(scope: DataScope, _p: Principal, target: ScopeTarget) -> ColumnElement[bool]:
    return _by_job_column("location_id", scope, target)


def _department(scope: DataScope, _p: Principal, target: ScopeTarget) -> ColumnElement[bool]:
    return _by_job_column("department_id", scope, target)


def _department_subtree(
    scope: DataScope, _p: Principal, target: ScopeTarget
) -> ColumnElement[bool]:
    if target.employee_column is None or scope.scope_id is None:
        return false()
    root = select(departments.c.path).where(departments.c.id == scope.scope_id).scalar_subquery()
    inside = select(departments.c.id).where(departments.c.path.op("<@")(root))
    return target.employee_column.in_(
        select(current_jobs.c.employee_id).where(current_jobs.c.department_id.in_(inside))
    )


def _direct_reports(
    _s: DataScope, principal: Principal, target: ScopeTarget
) -> ColumnElement[bool]:
    if target.employee_column is None:
        return false()
    return target.employee_column.in_(
        select(current_jobs.c.employee_id).where(
            current_jobs.c.manager_employee_id == my_employee_id(principal)
        )
    )


def _all_reports(_s: DataScope, principal: Principal, target: ScopeTarget) -> ColumnElement[bool]:
    if target.employee_column is None:
        return false()
    return target.employee_column.in_(
        select(hierarchy.c.descendant_employee_id).where(
            hierarchy.c.ancestor_employee_id == my_employee_id(principal), hierarchy.c.depth > 0
        )
    )


def _self(_s: DataScope, principal: Principal, target: ScopeTarget) -> ColumnElement[bool]:
    if target.employee_column is not None:
        return target.employee_column == my_employee_id(principal)
    if target.user_column is not None and principal.user_id is not None:
        return target.user_column == principal.user_id
    return false()


SCOPE_RESOLVERS.register(ScopeType.LEGAL_ENTITY, _legal_entity)
SCOPE_RESOLVERS.register(ScopeType.LOCATION, _location)
SCOPE_RESOLVERS.register(ScopeType.DEPARTMENT, _department)
SCOPE_RESOLVERS.register(ScopeType.DEPARTMENT_SUBTREE, _department_subtree)
SCOPE_RESOLVERS.register(ScopeType.DIRECT_REPORTS, _direct_reports)
SCOPE_RESOLVERS.register(ScopeType.ALL_REPORTS, _all_reports)
# Replaces platform's `self`, which only knows user-owned rows; this one also knows employees.
SCOPE_RESOLVERS.register(ScopeType.SELF, _self)
