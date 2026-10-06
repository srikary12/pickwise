# SPDX-License-Identifier: AGPL-3.0-only
"""Global search: the provider registry, permission and tenant filtering, and the limits."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.dependencies import Authorized
from pickwise.platform.search import SEARCH_PROVIDERS, SearchHit, SearchProvider
from pickwise.platform.search.users import like_pattern
from tests.integration.conftest import MakeApi
from tests.integration.support import Env, Tenant

pytestmark = pytest.mark.db

SEEN = uuid.uuid4()


async def _things(_s: AsyncSession, _a: Authorized, query: str, limit: int) -> list[SearchHit]:
    return [SearchHit("thing", SEEN, f"Thing {query}", None, "/things")][:limit]


@pytest.fixture
def thing_provider() -> Iterator[None]:
    SEARCH_PROVIDERS.register(SearchProvider("thing", "Things", "platform.audit.read", _things))
    yield
    SEARCH_PROVIDERS.unregister("thing")


async def test_finds_users_in_my_tenant_only(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    other = await env.create_tenant()
    mine = await env.add_account(tenant, "employee", email="zelda.search@acme.test")
    await env.add_account(other, "employee", email="zelda.search@globex.test")
    api = await (await make_api()).sign_in(tenant.admin)

    groups = (await api.get("/v1/search", params={"q": "zelda.search"})).json()["groups"]

    assert [g["kind"] for g in groups] == ["user"]
    assert [h["subtitle"] for h in groups[0]["hits"]] == ["zelda.search@acme.test"]
    assert groups[0]["hits"][0]["id"] == str(mine.user_id)


async def test_wildcards_in_the_query_are_literal(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    await env.add_account(tenant, "employee", email="wild.card@acme.test")
    api = await (await make_api()).sign_in(tenant.admin)
    assert (await api.get("/v1/search", params={"q": "%%"})).json()["groups"] == []
    assert (await api.get("/v1/search", params={"q": "wild_card"})).json()["groups"] == []
    assert like_pattern("a%b_c\\d") == "%a\\%b\\_c\\\\d%"


async def test_a_provider_only_runs_for_holders_of_its_permission(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    employee = await env.add_account(tenant, "employee")
    api = await (await make_api()).sign_in(employee)
    # Employees can search, but hold neither platform.users.read nor platform.audit.read.
    assert (await api.get("/v1/search", params={"q": "admin"})).json() == {"groups": []}


async def test_registered_providers_are_used(
    thing_provider: None, make_api: MakeApi, tenant: Tenant
) -> None:
    api = await (await make_api()).sign_in(tenant.admin)
    groups = (await api.get("/v1/search", params={"q": "abc", "kinds": "thing"})).json()["groups"]
    assert groups == [
        {
            "kind": "thing",
            "label": "Things",
            "hits": [
                {
                    "kind": "thing",
                    "id": str(SEEN),
                    "title": "Thing abc",
                    "subtitle": None,
                    "link": "/things",
                }
            ],
        }
    ]
    # `kinds` narrows the search.
    only_users = (await api.get("/v1/search", params={"q": "abc", "kinds": "user"})).json()
    assert all(g["kind"] == "user" for g in only_users["groups"])


async def test_short_and_long_queries_are_rejected(make_api: MakeApi, tenant: Tenant) -> None:
    api = await (await make_api()).sign_in(tenant.admin)
    assert (await api.get("/v1/search", params={"q": "a"})).status_code == 422
    assert (await api.get("/v1/search", params={"q": "x" * 101})).status_code == 422
    assert (await api.get("/v1/search")).status_code == 422


async def test_anonymous_callers_are_refused(make_api: MakeApi) -> None:
    api = await make_api()
    assert (await api.get("/v1/search", params={"q": "abc"})).status_code == 401
