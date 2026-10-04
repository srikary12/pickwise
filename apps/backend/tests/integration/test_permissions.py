# SPDX-License-Identifier: AGPL-3.0-only
"""Authorization: the route guard, the permission matrix, and data scopes."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from sqlalchemy import column, select, table

from pickwise.platform.auth.dependencies import PERMISSION_MARKER, PUBLIC_MARKER, SIGNED_IN_MARKER
from pickwise.platform.permissions import PERMISSIONS
from pickwise.platform.rbac.principal import Principal
from pickwise.platform.rbac.system_roles import SYSTEM_ROLES, default_grants
from pickwise.platform.scopes import (
    SCOPE_RESOLVERS,
    DataScope,
    ScopeResolverRegistry,
    ScopeTarget,
    ScopeType,
    scope_filter,
)
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database
from tests.integration.conftest import MakeApi
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db

MARKERS = (PERMISSION_MARKER, SIGNED_IN_MARKER, PUBLIC_MARKER)


@dataclass(frozen=True, slots=True)
class RouteInfo:
    method: str
    path: str
    permission: str | None
    markers: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}"


def _markers(dependant: Dependant) -> Iterator[tuple[str, object]]:
    for dep in dependant.dependencies:
        for marker in MARKERS:
            if hasattr(dep.call, marker):
                yield marker, getattr(dep.call, marker)
        yield from _markers(dep)


def _flat_routes(app: FastAPI) -> Iterator[tuple[str, set[str], Dependant]]:
    """Every endpoint with its path, methods and dependency tree.

    FastAPI includes routers lazily (``_IncludedRouter``); its ``effective_route_contexts``
    resolves prefixes and router-level dependencies, so the markers we find are exactly
    what a request would run.
    """
    for route in app.routes:
        if hasattr(route, "effective_route_contexts"):
            for ctx in route.effective_route_contexts():
                yield ctx.path, set(ctx.methods or ()), ctx.dependant
        elif isinstance(route, APIRoute):
            yield route.path, set(route.methods or ()), route.dependant


def routes_of(app: FastAPI) -> list[RouteInfo]:
    found: list[RouteInfo] = []
    for path, methods, dependant in _flat_routes(app):
        marks = list(_markers(dependant))
        permission = next((str(v) for m, v in marks if m == PERMISSION_MARKER), None)
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            found.append(RouteInfo(method, path, permission, tuple(sorted({m for m, _ in marks}))))
    assert len(found) > 40, "route discovery found too few routes; FastAPI internals changed?"
    return found


def concrete(path: str) -> str:
    out = path
    while "{" in out:
        start, end = out.index("{"), out.index("}")
        out = out[:start] + str(uuid.uuid4()) + out[end + 1 :]
    return out


async def call(api: Api, route: RouteInfo, headers: dict[str, str] | None = None) -> int:
    response = await api.http.request(
        route.method,
        concrete(route.path),
        json={} if route.method in ("POST", "PUT", "PATCH") else None,
        headers=headers,
    )
    if response.status_code == 403:
        # Only a permission refusal counts as "denied"; keep the code to tell them apart.
        assert response.json()["error"]["code"] == "permission_denied", response.text
    return response.status_code


# --- the route guard ----------------------------------------------------------------------------


def test_every_route_declares_its_access(app: FastAPI) -> None:
    undeclared = [r.id for r in routes_of(app) if not r.markers]
    assert undeclared == [], f"routes with no require()/signed_in()/public marker: {undeclared}"


def test_public_routes_are_an_explicit_short_list(app: FastAPI) -> None:
    public = {r.id for r in routes_of(app) if PUBLIC_MARKER in r.markers}
    assert public == {
        "GET /healthz",
        "GET /readyz",
        "GET /v1/auth/csrf",
        "POST /v1/auth/login",
        "POST /v1/auth/logout",
        "POST /v1/auth/password/forgot",
        "POST /v1/auth/password/reset",
        "POST /v1/auth/email/verify",
        "GET /v1/auth/invites/{token}",
        "POST /v1/auth/invites/accept",
        "GET /v1/auth/sso/discover",
        "GET /v1/auth/sso/start",
        "GET /v1/auth/sso/callback",
        "POST /v1/signup",
        "POST /v1/signup/verify",
        "GET /v1/signup/{signup_id}",
    }


def test_every_declared_permission_exists_in_the_catalog(app: FastAPI) -> None:
    declared = {r.permission for r in routes_of(app) if r.permission}
    assert declared <= {p.code for p in PERMISSIONS.all()}


async def test_unauthenticated_requests_get_401_everywhere(app: FastAPI, make_api: MakeApi) -> None:
    api = await make_api()
    for route in routes_of(app):
        if route.permission or SIGNED_IN_MARKER in route.markers:
            assert await call(api, route) == 401, route.id


# --- the permission matrix ------------------------------------------------------------------------


async def test_permission_matrix_for_every_system_role(
    app: FastAPI, env: Env, tenant: Tenant, make_api: MakeApi
) -> None:
    guarded = [r for r in routes_of(app) if r.permission]
    assert len(guarded) >= 15
    for role in SYSTEM_ROLES:
        account = await env.add_account(tenant, role.key)
        api = await (await make_api()).sign_in(account)
        held = set(default_grants(role.key))
        for route in guarded:
            status = await call(api, route)
            if route.permission in held:
                assert status != 403, f"{role.key} should reach {route.id}, got {status}"
            else:
                assert status == 403, f"{role.key} must be refused {route.id}, got {status}"


async def test_permission_matrix_for_single_permission_api_keys(
    app: FastAPI, make_api: MakeApi, tenant: Tenant
) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    guarded = [r for r in routes_of(app) if r.permission]
    for permission in sorted({r.permission for r in guarded if r.permission}):
        created = await admin.post(
            "/v1/admin/api-keys", {"name": f"only {permission}", "scopes": [permission]}
        )
        assert created.status_code == 201, created.text
        client = await make_api()
        headers = {"Authorization": f"Bearer {created.json()['key']}"}
        for route in guarded:
            status = await call(client, route, headers)
            if route.permission == permission:
                assert status != 403, f"key[{permission}] should reach {route.id}, got {status}"
            else:
                assert status == 403, f"key[{permission}] must be refused {route.id}, got {status}"


async def test_a_custom_role_without_permissions_reaches_nothing(
    app: FastAPI, env: Env, tenant: Tenant, make_api: MakeApi
) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    role = (await admin.post("/v1/admin/roles", {"key": "empty_role", "name": "Empty"})).json()
    person = await env.add_account(tenant, "interviewer")
    members = {m["email"]: m for m in (await admin.get("/v1/admin/users")).json()}
    await admin.post(
        "/v1/admin/role-assignments",
        {"membership_id": members[person.email]["membership_id"], "role_id": role["id"]},
    )
    api = await (await make_api()).sign_in(person)
    # The interviewer system role still holds its own defaults (the platform file permissions).
    own = set(default_grants("interviewer"))
    for route in routes_of(app):
        if route.permission and route.permission not in own:
            assert await call(api, route) == 403, route.id


# --- data scopes ----------------------------------------------------------------------------------


@pytest.fixture
async def api_db(api_settings: object) -> Iterator[Database]:  # type: ignore[misc]
    import os

    from pydantic import SecretStr

    from pickwise.shared.settings import Settings

    db = Database.from_settings(
        Settings(
            database_user="pickwise_api", database_password=SecretStr(os.environ["PG_API_PASSWORD"])
        )
    )
    yield db
    await db.dispose()


_memberships = table("memberships", column("user_id"), column("status"), schema="platform")


async def test_scope_filter_with_a_direct_reports_resolver(
    env: Env, tenant: Tenant, api_db: Database
) -> None:
    manager = await env.add_account(tenant, "manager")
    report_a = await env.add_account(tenant, "employee")
    report_b = await env.add_account(tenant, "employee")
    stranger = await env.add_account(tenant, "employee")
    reports = {manager.user_id: {report_a.user_id, report_b.user_id}}

    registry = ScopeResolverRegistry()
    registry.register(ScopeType.TENANT, SCOPE_RESOLVERS.resolver(ScopeType.TENANT))  # type: ignore[arg-type]
    registry.register(ScopeType.SELF, SCOPE_RESOLVERS.resolver(ScopeType.SELF))  # type: ignore[arg-type]
    registry.register(
        ScopeType.DIRECT_REPORTS,
        lambda _scope, principal, target: target.user_column.in_(  # type: ignore[union-attr]
            reports.get(principal.user_id, set())  # type: ignore[arg-type]
        ),
    )
    principal = Principal(ActorType.USER, tenant.id, user_id=manager.user_id)
    target = ScopeTarget(user_column=_memberships.c.user_id)

    async def visible(*scopes: DataScope, reg: ScopeResolverRegistry = registry) -> set[uuid.UUID]:
        ctx = RequestContext(ActorType.USER, tenant.id, manager.user_id)
        async with api_db.tenant_session(ctx) as session:
            rows = await session.execute(
                select(_memberships.c.user_id).where(scope_filter(scopes, principal, target, reg))
            )
            return {r[0] for r in rows}

    everyone = {
        tenant.admin.user_id,
        manager.user_id,
        report_a.user_id,
        report_b.user_id,
        stranger.user_id,
    }
    assert await visible(DataScope(ScopeType.DIRECT_REPORTS)) == {
        report_a.user_id,
        report_b.user_id,
    }
    assert await visible(DataScope(ScopeType.SELF)) == {manager.user_id}
    assert await visible(DataScope(ScopeType.TENANT)) == everyone
    assert await visible(DataScope(ScopeType.SELF), DataScope(ScopeType.DIRECT_REPORTS)) == {
        manager.user_id,
        report_a.user_id,
        report_b.user_id,
    }
    # Without a registered resolver the scope fails closed.
    assert await visible(DataScope(ScopeType.DIRECT_REPORTS), reg=ScopeResolverRegistry()) == set()
    assert await visible() == set()


async def test_a_manager_sees_only_their_reports_through_the_api(
    make_api: MakeApi, env: Env, tenant: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = await env.add_account(tenant, "manager")
    report = await env.add_account(tenant, "employee")
    other = await env.add_account(tenant, "employee")
    admin = await (await make_api()).sign_in(tenant.admin)
    role = (
        await admin.post(
            "/v1/admin/roles",
            {"key": "team_reader", "name": "Team reader", "permissions": ["platform.users.read"]},
        )
    ).json()
    members = {m["email"]: m for m in (await admin.get("/v1/admin/users")).json()}
    for scope in ("direct_reports", "self"):
        assert (
            await admin.post(
                "/v1/admin/role-assignments",
                {
                    "membership_id": members[manager.email]["membership_id"],
                    "role_id": role["id"],
                    "scope_type": scope,
                },
            )
        ).status_code == 201

    api = await (await make_api()).sign_in(manager)
    me = (await api.get("/v1/me")).json()
    assert sorted(me["permissions"]["platform.users.read"]) == ["direct_reports", "self"]

    # Core registers the real resolver in Phase 5; until then a scope with no
    # resolver fails closed and only the self scope yields rows.
    seen = {m["email"] for m in (await api.get("/v1/admin/users")).json()}
    assert seen == {manager.email}

    reports = {manager.user_id: {report.user_id}}
    monkeypatch.setattr(
        SCOPE_RESOLVERS,
        "_resolvers",
        {
            **SCOPE_RESOLVERS._resolvers,
            ScopeType.DIRECT_REPORTS: lambda _s, principal, target: target.user_column.in_(
                reports.get(principal.user_id, set())
            ),
        },
    )
    seen = {m["email"] for m in (await api.get("/v1/admin/users")).json()}
    assert seen == {manager.email, report.email}
    assert other.email not in seen
