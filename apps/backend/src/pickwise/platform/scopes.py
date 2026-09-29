# SPDX-License-Identifier: AGPL-3.0-only
"""Data scopes (CLAUDE.md rule 16) and the resolver registry.

A role assignment grants a permission within a scope. Repositories turn the
caller's scopes into a SQL filter with ``scope_filter``. Platform can resolve only
``tenant`` and ``self``; core registers the org-based resolvers (department
subtree, direct/all reports, …) in Phase 5 without platform importing core. A
scope with no registered resolver denies, so a missing resolver fails closed.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import ColumnElement, false, or_, true

from pickwise.platform.rbac.principal import Principal


class ScopeType(StrEnum):
    TENANT = "tenant"
    LEGAL_ENTITY = "legal_entity"
    LOCATION = "location"
    DEPARTMENT = "department"
    DEPARTMENT_SUBTREE = "department_subtree"
    DIRECT_REPORTS = "direct_reports"
    ALL_REPORTS = "all_reports"
    SELF = "self"


ENTITY_SCOPES = frozenset(
    {ScopeType.LEGAL_ENTITY, ScopeType.LOCATION, ScopeType.DEPARTMENT, ScopeType.DEPARTMENT_SUBTREE}
)


@dataclass(frozen=True, slots=True)
class DataScope:
    type: ScopeType
    scope_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ScopeTarget:
    """How a queried table relates to scopes. Modules fill in what applies:
    platform rows owned by a user set ``user_column``; employee-owned rows set
    ``employee_column`` (used by core's resolvers)."""

    user_column: ColumnElement[uuid.UUID] | None = None
    employee_column: ColumnElement[uuid.UUID] | None = None
    extra: dict[str, ColumnElement[uuid.UUID]] = field(default_factory=dict)


ScopeResolver = Callable[[DataScope, Principal, ScopeTarget], ColumnElement[bool]]


class ScopeResolverRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[ScopeType, ScopeResolver] = {}

    def register(self, scope_type: ScopeType, resolver: ScopeResolver) -> None:
        self._resolvers[scope_type] = resolver

    def unregister(self, scope_type: ScopeType) -> None:
        self._resolvers.pop(scope_type, None)

    def resolver(self, scope_type: ScopeType) -> ScopeResolver | None:
        return self._resolvers.get(scope_type)


SCOPE_RESOLVERS = ScopeResolverRegistry()


def _tenant(_scope: DataScope, _principal: Principal, _target: ScopeTarget) -> ColumnElement[bool]:
    # RLS already limits every query to the caller's tenant.
    return true()


def _self(_scope: DataScope, principal: Principal, target: ScopeTarget) -> ColumnElement[bool]:
    if target.user_column is not None and principal.user_id is not None:
        return target.user_column == principal.user_id
    return false()


SCOPE_RESOLVERS.register(ScopeType.TENANT, _tenant)
SCOPE_RESOLVERS.register(ScopeType.SELF, _self)


def scope_filter(
    scopes: tuple[DataScope, ...],
    principal: Principal,
    target: ScopeTarget,
    registry: ScopeResolverRegistry = SCOPE_RESOLVERS,
) -> ColumnElement[bool]:
    """OR of every scope's filter; no scopes (or no resolver) means no rows."""
    clauses = []
    for scope in scopes:
        resolver = registry.resolver(scope.type)
        clauses.append(resolver(scope, principal, target) if resolver else false())
    if not clauses:
        return false()
    return or_(*clauses)
