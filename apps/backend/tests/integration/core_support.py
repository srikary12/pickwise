# SPDX-License-Identifier: AGPL-3.0-only
"""Helpers for tests that need an organisation and employees."""

import datetime
import uuid
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from tests.integration.support import Account, Api, Env, Tenant


def today() -> datetime.date:
    """The IST calendar day, which is what core.today() returns."""
    return datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date()


@dataclass(frozen=True, slots=True)
class Org:
    entity: str
    location: str
    location_2: str
    department: str
    department_2: str
    designation: str
    designation_2: str
    grade: str

    def job(self, **overrides: Any) -> dict[str, Any]:
        return {
            "legal_entity_id": self.entity,
            "location_id": self.location,
            "department_id": self.department,
            "designation_id": self.designation,
            "employment_type": "full_time",
            **overrides,
        }


async def post(api: Api, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await api.post(path, body)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def make_org(api: Api) -> Org:
    entity = await post(
        api, "/v1/org/legal-entities", {"name": "Acme India", "legal_name": "Acme India Pvt Ltd"}
    )
    locations = []
    for code, state in (("BLR", "IN-KA"), ("HYD", "IN-TS")):
        locations.append(
            await post(
                api,
                "/v1/org/locations",
                {
                    "legal_entity_id": entity["id"],
                    "code": code,
                    "name": code,
                    "state_code": state,
                },
            )
        )
    departments = [
        await post(api, "/v1/org/departments", {"code": code, "name": code})
        for code in ("ENG", "SALES")
    ]
    designations = [
        await post(api, "/v1/org/designations", {"code": code, "name": name})
        for code, name in (("SE", "Engineer"), ("SSE", "Senior Engineer"))
    ]
    grade = await post(api, "/v1/org/grades", {"code": "L3", "name": "Level 3", "rank": 3})
    return Org(
        entity["id"],
        locations[0]["id"],
        locations[1]["id"],
        departments[0]["id"],
        departments[1]["id"],
        designations[0]["id"],
        designations[1]["id"],
        grade["id"],
    )


async def make_employee(
    api: Api,
    org: Org,
    first_name: str,
    *,
    joining: datetime.date | None = None,
    activate: bool = True,
    job: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Create an employee (and by default make them active)."""
    body = {
        "first_name": first_name,
        "last_name": "Tester",
        "date_of_joining": (joining or today() - datetime.timedelta(days=100)).isoformat(),
        "job": org.job(**(job or {})),
        **extra,
    }
    employee = await post(api, "/v1/employees", body)
    if activate:
        response = await api.post(
            f"/v1/employees/{employee['id']}/status",
            {"to": "active", "row_version": employee["row_version"]},
        )
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result
    return employee


async def scoped_account(
    env: Env,
    tenant: Tenant,
    role_key: str,
    scope_type: str,
    scope_id: uuid.UUID | str | None = None,
) -> Account:
    """A member whose only role assignment is ``role_key`` at the given scope."""
    account = await env.add_account(tenant, role_key)
    await env.sql(
        "DELETE FROM platform.role_assignments WHERE membership_id = :m", m=account.membership_id
    )
    await env.sql(
        "INSERT INTO platform.role_assignments (tenant_id, membership_id, role_id, scope_type, "
        "scope_id) SELECT :t, :m, r.id, :st, :sid FROM platform.roles r "
        "WHERE r.tenant_id = :t AND r.key = :k",
        t=tenant.id,
        m=account.membership_id,
        st=scope_type,
        sid=uuid.UUID(str(scope_id)) if scope_id else None,
        k=role_key,
    )
    return account


async def link(api: Api, employee: dict[str, Any], account: Account) -> dict[str, Any]:
    response = await api.put(
        f"/v1/employees/{employee['id']}/user",
        {"membership_id": str(account.membership_id), "row_version": employee["row_version"]},
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result
