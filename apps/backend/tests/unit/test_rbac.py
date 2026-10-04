# SPDX-License-Identifier: AGPL-3.0-only
import uuid

import pytest
from sqlalchemy import Column, MetaData, Table, Uuid, select

from pickwise.platform.permissions import PERMISSIONS, Permission, PermissionRegistry
from pickwise.platform.rbac.principal import Principal
from pickwise.platform.rbac.system_roles import SYSTEM_ROLES, default_grants
from pickwise.platform.scopes import (
    DataScope,
    ScopeResolverRegistry,
    ScopeTarget,
    ScopeType,
    scope_filter,
)
from pickwise.shared.context import ActorType

things = Table("things", MetaData(), Column("owner_user_id", Uuid), Column("employee_id", Uuid))
USER = uuid.uuid4()
PRINCIPAL = Principal(ActorType.USER, uuid.uuid4(), user_id=USER, membership_id=uuid.uuid4())
TARGET = ScopeTarget(user_column=things.c.owner_user_id, employee_column=things.c.employee_id)


def _sql(clause: object) -> str:
    return str(select(things).where(clause).compile(compile_kwargs={"literal_binds": True}))  # type: ignore[arg-type]


def test_every_default_role_is_a_system_role() -> None:
    keys = {r.key for r in SYSTEM_ROLES}
    assert len(keys) == 9
    for p in PERMISSIONS.all():
        assert set(p.default_roles) <= keys, p.code


def test_tenant_admin_holds_every_platform_permission() -> None:
    platform = {p.code for p in PERMISSIONS.all() if p.module == "platform"}
    assert platform <= set(default_grants("tenant_admin"))
    # Everyone may upload and read their own files; nothing else is a default.
    assert set(default_grants("employee")) == {
        "platform.custom_fields.read",
        "platform.files.read",
        "platform.files.upload",
    }


def test_registry_rejects_conflicting_definitions() -> None:
    registry = PermissionRegistry()
    registry.register(Permission("x.y.read", "x", "one"))
    registry.register(Permission("x.y.read", "x", "one"))  # identical is fine
    with pytest.raises(ValueError, match="twice"):
        registry.register(Permission("x.y.read", "x", "different"))


def test_no_scopes_means_no_rows() -> None:
    assert "false" in _sql(scope_filter((), PRINCIPAL, TARGET)).lower()


def test_tenant_scope_is_unfiltered_and_self_scope_matches_the_user() -> None:
    assert "true" in _sql(scope_filter((DataScope(ScopeType.TENANT),), PRINCIPAL, TARGET)).lower()
    assert USER.hex in _sql(scope_filter((DataScope(ScopeType.SELF),), PRINCIPAL, TARGET)).replace(
        "-", ""
    )


def test_unregistered_scope_types_fail_closed() -> None:
    registry = ScopeResolverRegistry()
    clause = scope_filter((DataScope(ScopeType.DIRECT_REPORTS),), PRINCIPAL, TARGET, registry)
    assert "false" in _sql(clause).lower()


def test_scopes_combine_with_or() -> None:
    registry = ScopeResolverRegistry()
    registry.register(ScopeType.DIRECT_REPORTS, lambda s, p, t: t.employee_column == s.scope_id)  # type: ignore[arg-type,return-value]
    registry.register(ScopeType.SELF, lambda s, p, t: t.user_column == p.user_id)  # type: ignore[arg-type,return-value]
    report = uuid.uuid4()
    sql = _sql(
        scope_filter(
            (DataScope(ScopeType.DIRECT_REPORTS, report), DataScope(ScopeType.SELF)),
            PRINCIPAL,
            TARGET,
            registry,
        )
    )
    assert " OR " in sql
    assert report.hex in sql.replace("-", "")
