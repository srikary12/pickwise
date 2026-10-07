# SPDX-License-Identifier: AGPL-3.0-only
"""Identity documents and bank accounts: encryption, masking, duplicates, aadhaar modes, reveal."""

import datetime
from typing import Any

import pytest

from pickwise.core.identity.service import verhoeff_valid
from tests.integration.conftest import MakeApi
from tests.integration.core_support import (
    Org,
    link,
    make_employee,
    make_org,
    scoped_account,
    today,
)
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db

PAN = "ABCDE1234F"


def aadhaar_number(seed: str = "23456789012") -> str:
    """An 11-digit prefix plus the check digit that makes it a valid Aadhaar number."""
    for check in "0123456789":
        if verhoeff_valid(seed + check):
            return seed + check
    raise AssertionError("no check digit")


async def setup(
    make_api: MakeApi, tenant: Tenant
) -> tuple[Api, Org, dict[str, Any], dict[str, Any]]:
    api = await (await make_api()).sign_in(tenant.admin)
    org = await make_org(api)
    return api, org, await make_employee(api, org, "Ann"), await make_employee(api, org, "Bob")


async def add(api: Api, employee: dict[str, Any], doc_type: str, value: str, **extra: Any) -> Any:
    return await api.post(
        f"/v1/employees/{employee['id']}/identity", {"doc_type": doc_type, "value": value, **extra}
    )


async def reveal(api: Api, entity_type: str, entity_id: str, field: str) -> Any:
    return await api.post(
        "/v1/pii/reveal", {"entity_type": entity_type, "entity_id": entity_id, "field": field}
    )


# --- identity documents ----------------------------------------------------------------------


async def test_a_pan_is_encrypted_masked_and_revealed_with_an_audit_trail(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api, _, ann, _ = await setup(make_api, tenant)
    created = await add(api, ann, "pan", "abcde 1234f", name_as_per_doc="Ann Tester")
    assert created.status_code == 201, created.text
    document = created.json()
    assert document["last4"] == "234F"
    assert document["has_value"] is True
    assert PAN not in created.text
    assert "ABCDE" not in created.text
    listed = await api.get(f"/v1/employees/{ann['id']}/identity")
    assert PAN not in listed.text

    row = (
        await env.sql(
            "SELECT value_enc, value_bidx, bidx_key_version FROM core.identity_documents "
            "WHERE id = :id",
            id=document["id"],
        )
    )[0]
    assert PAN.encode() not in bytes(row[0])
    assert len(bytes(row[1])) == 32

    revealed = await reveal(api, "identity_document", document["id"], "value")
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["value"] == PAN
    events = await env.sql(
        "SELECT entity_table, changes FROM audit.events WHERE action = 'pii.reveal' "
        "AND entity_id = :id",
        id=document["id"],
    )
    assert len(events) == 1
    assert PAN not in str(events[0])
    # The row-change audit never recorded the number either.
    changes = await env.sql(
        "SELECT changes::text FROM audit.events WHERE entity_table = 'identity_documents' "
        "AND entity_id = :id AND action = 'insert'",
        id=document["id"],
    )
    assert changes
    assert PAN not in changes[0][0]
    assert "[set]" in changes[0][0]


async def test_a_number_can_belong_to_one_employee_in_a_tenant(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api, _, ann, bob = await setup(make_api, tenant)
    assert (await add(api, ann, "pan", PAN)).status_code == 201
    clash = await add(api, bob, "pan", PAN.lower())
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "duplicate_identity"
    assert str(ann["id"]) not in clash.text  # doesn't say whose it is
    # Another tenant has its own blind-index key, and its own PAN space.
    other = await env.create_tenant()
    other_api = await (await make_api()).sign_in(other.admin)
    other_org = await make_org(other_api)
    stranger = await make_employee(other_api, other_org, "Cy")
    assert (await add(other_api, stranger, "pan", PAN)).status_code == 201
    # Replacing an employee's own PAN with the same number is fine (they are the owner).
    again = await add(api, ann, "pan", PAN)
    assert again.status_code == 201


async def test_a_new_document_replaces_the_current_one_and_keeps_history(
    make_api: MakeApi, tenant: Tenant
) -> None:
    api, _, ann, _ = await setup(make_api, tenant)
    first = (await add(api, ann, "passport", "K1234567", expires_on="2030-01-01")).json()
    second = (await add(api, ann, "passport", "K7654321", expires_on="2035-01-01")).json()
    current = (await api.get(f"/v1/employees/{ann['id']}/identity")).json()
    assert [d["id"] for d in current] == [second["id"]]
    history = (
        await api.get(f"/v1/employees/{ann['id']}/identity", params={"history": "true"})
    ).json()
    assert {d["id"]: d["is_current"] for d in history} == {first["id"]: False, second["id"]: True}


@pytest.mark.parametrize(
    ("doc_type", "value"),
    [
        ("pan", "ABCDE12345"),
        ("pan", "12345ABCDE"),
        ("aadhaar", "123456789012"),  # fails the check digit
        ("aadhaar", "023456789012"),  # can't start with 0 or 1
        ("passport", "12345678"),
        ("uan", "12345"),
        ("esic_ip", "123456789"),
        ("voter_id", "AB12345678"),
    ],
)
async def test_bad_numbers_are_refused(
    make_api: MakeApi, tenant: Tenant, doc_type: str, value: str
) -> None:
    api, _, ann, _ = await setup(make_api, tenant)
    refused = await add(api, ann, doc_type, value)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "invalid_identity"


async def test_other_formats_are_accepted(make_api: MakeApi, tenant: Tenant) -> None:
    api, _, ann, _ = await setup(make_api, tenant)
    for doc_type, value in (
        ("uan", "123456789012"),
        ("esic_ip", "1234567890"),
        ("passport", "K1234567"),
        ("voter_id", "ABC1234567"),
        ("driving_licence", "KA0120110012345"),
        ("visa", "V1234567"),
    ):
        assert (await add(api, ann, doc_type, value)).status_code == 201, doc_type


async def test_aadhaar_defaults_to_the_last_four_digits_only(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api, _, ann, bob = await setup(make_api, tenant)
    number = aadhaar_number()
    created = await add(api, ann, "aadhaar", number)
    assert created.status_code == 201, created.text
    assert created.json()["last4"] == number[-4:]
    assert created.json()["has_value"] is False
    assert number not in created.text
    stored = (
        await env.sql(
            "SELECT value_enc, value_bidx, value_last4 FROM core.identity_documents WHERE id = :id",
            id=created.json()["id"],
        )
    )[0]
    assert stored[0] is None
    assert stored[1] is None  # not even a hash of the number
    assert stored[2] == number[-4:]
    # No uniqueness in this mode, and nothing to reveal.
    assert (await add(api, bob, "aadhaar", number)).status_code == 201
    gone = await reveal(api, "identity_document", created.json()["id"], "value")
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "not_stored"
    # Four digits alone are accepted too.
    assert (await add(api, ann, "aadhaar", "4321")).status_code == 201


async def test_aadhaar_full_mode_is_encrypted_unique_and_revealable(
    make_api: MakeApi, env: Env
) -> None:
    tenant = await env.create_tenant(settings={"aadhaar_mode": "full"})
    api = await (await make_api()).sign_in(tenant.admin)
    org = await make_org(api)
    ann = await make_employee(api, org, "Ann")
    bob = await make_employee(api, org, "Bob")
    number = aadhaar_number("34567890123")
    created = await add(api, ann, "aadhaar", number)
    assert created.status_code == 201
    assert created.json()["has_value"] is True
    duplicate = await add(api, bob, "aadhaar", number)
    assert duplicate.status_code == 409
    revealed = await reveal(api, "identity_document", created.json()["id"], "value")
    assert revealed.json()["value"] == number
    short = await add(api, bob, "aadhaar", "4321")
    assert short.status_code == 422  # full mode wants the whole number


async def test_verification(make_api: MakeApi, tenant: Tenant) -> None:
    api, _, ann, _ = await setup(make_api, tenant)
    document = (await add(api, ann, "pan", PAN)).json()
    assert document["verification_status"] == "unverified"
    path = f"/v1/employees/{ann['id']}/identity/{document['id']}/verify"
    verified = await api.post(path, {"status": "verified", "row_version": document["row_version"]})
    assert verified.status_code == 200
    assert verified.json()["verification_status"] == "verified"
    assert verified.json()["verified_at"] is not None
    stale = await api.post(path, {"status": "rejected", "row_version": document["row_version"]})
    assert stale.status_code == 409


async def test_permissions_and_scope_for_identity_and_reveal(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api, org, ann, bob = await setup(make_api, tenant)
    ann_doc = (await add(api, ann, "pan", PAN)).json()
    bob_doc = (await add(api, bob, "pan", "ZZZZZ9999Z")).json()

    # HR ops can see employees but not identity documents.
    ops = await (await make_api()).sign_in(await env.add_account(tenant, "hr_ops"))
    assert (await ops.get(f"/v1/employees/{ann['id']}")).status_code == 200
    assert (await ops.get(f"/v1/employees/{ann['id']}/identity")).status_code == 403
    # HR admin can read and add, but can't reveal a number.
    hr = await (await make_api()).sign_in(await env.add_account(tenant, "hr_admin"))
    assert (await hr.get(f"/v1/employees/{ann['id']}/identity")).status_code == 200
    denied = await reveal(hr, "identity_document", ann_doc["id"], "value")
    assert denied.status_code == 403
    # An admin limited to one location can't reach, or reveal for, someone elsewhere.
    local = await scoped_account(env, tenant, "tenant_admin", "location", org.location_2)
    local_api = await (await make_api()).sign_in(local)
    assert (await local_api.get(f"/v1/employees/{ann['id']}/identity")).status_code == 404
    assert (await reveal(local_api, "identity_document", bob_doc["id"], "value")).status_code == 404
    # Unknown fields and documents are plain 404s.
    assert (await reveal(api, "identity_document", ann_doc["id"], "nope")).status_code == 404


async def test_people_see_their_own_documents_masked(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api, _, ann, _ = await setup(make_api, tenant)
    await add(api, ann, "pan", PAN)
    account = await env.add_account(tenant, "employee")
    await link(api, (await api.get(f"/v1/employees/{ann['id']}")).json(), account)
    me = await (await make_api()).sign_in(account)
    own = await me.get("/v1/me/employee/identity")
    assert own.status_code == 200
    assert own.json()[0]["last4"] == "234F"
    assert PAN not in own.text


# --- bank accounts ---------------------------------------------------------------------------


def bank(**extra: Any) -> dict[str, Any]:
    return {
        "account_holder_name": "Ann Tester",
        "account_number": "123456789012",
        "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank",
        **extra,
    }


async def test_bank_accounts_are_masked_and_the_primary_changes_over_time(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api, _, ann, _ = await setup(make_api, tenant)
    path = f"/v1/employees/{ann['id']}/bank-accounts"
    first = await api.post(path, bank())
    assert first.status_code == 201, first.text
    assert first.json()["last4"] == "9012"
    assert "123456789012" not in first.text
    assert first.json()["is_primary"] is True
    raw = (
        await env.sql(
            "SELECT account_number_enc FROM core.bank_accounts WHERE id = :id",
            id=first.json()["id"],
        )
    )[0][0]
    assert b"123456789012" not in bytes(raw)

    start = today() + datetime.timedelta(days=10)
    second = await api.post(
        path,
        bank(
            account_number="998877665544",
            ifsc="ICIC0005678",
            bank_name="ICICI",
            valid_from=start.isoformat(),
        ),
    )
    assert second.status_code == 201, second.text
    accounts = (await api.get(path)).json()
    by_last4 = {a["last4"]: a for a in accounts}
    assert by_last4["9012"]["valid_to"] == (start - datetime.timedelta(days=1)).isoformat()
    assert by_last4["5544"]["valid_to"] is None
    # A non-primary account doesn't disturb the primary.
    extra = await api.post(path, bank(account_number="555500001111", is_primary=False))
    assert extra.status_code == 201
    assert len((await api.get(path)).json()) == 3

    revealed = await reveal(api, "bank_account", first.json()["id"], "account_number")
    assert revealed.json()["value"] == "123456789012"
    ended = await api.post(
        f"{path}/{second.json()['id']}/end", params={"row_version": second.json()["row_version"]}
    )
    assert ended.status_code == 200
    assert ended.json()["valid_to"] is not None


async def test_bank_validation_and_permissions(make_api: MakeApi, env: Env, tenant: Tenant) -> None:
    api, _, ann, _ = await setup(make_api, tenant)
    path = f"/v1/employees/{ann['id']}/bank-accounts"
    assert (await api.post(path, bank(ifsc="HDFC1001234"))).status_code == 422
    assert (await api.post(path, bank(account_number="12ab"))).status_code == 422
    # Payroll admins manage bank details, but have no business with identity documents; plain
    # HR ops can't see bank details at all.
    payroll = await (await make_api()).sign_in(await env.add_account(tenant, "payroll_admin"))
    assert (await payroll.get(path)).status_code == 200
    assert (await payroll.get(f"/v1/employees/{ann['id']}/identity")).status_code == 403
    ops = await (await make_api()).sign_in(await env.add_account(tenant, "hr_ops"))
    assert (await ops.get(path)).status_code == 403
