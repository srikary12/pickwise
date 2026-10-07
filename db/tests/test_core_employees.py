# SPDX-License-Identifier: AGPL-3.0-only
"""core employees: job-record overlap, primary bank account, nomination totals, identity checks."""

import uuid

import psycopg
import pytest
from conftest import Conn, Connect, SeededTenant, as_tenant

pytestmark = pytest.mark.db


def one(conn: Conn, sql: str, params: tuple[object, ...] = ()) -> uuid.UUID:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    assert isinstance(row[0], uuid.UUID)
    return row[0]


def org(conn: Conn) -> dict[str, uuid.UUID]:
    entity = one(
        conn,
        "INSERT INTO core.legal_entities (name, legal_name) VALUES ('E', 'E Ltd') RETURNING id",
    )
    return {
        "entity": entity,
        "location": one(
            conn,
            "INSERT INTO core.locations (legal_entity_id, code, name, state_code) "
            "VALUES (%s, 'L', 'L', 'IN-KA') RETURNING id",
            (entity,),
        ),
        "department": one(
            conn, "INSERT INTO core.departments (code, name) VALUES ('D', 'D') RETURNING id"
        ),
        "designation": one(
            conn, "INSERT INTO core.designations (code, name) VALUES ('X', 'X') RETURNING id"
        ),
    }


def employee(conn: Conn, code: str) -> uuid.UUID:
    return one(
        conn,
        "INSERT INTO core.employees (employee_code, first_name, date_of_joining) "
        "VALUES (%s, 'A', '2024-01-01') RETURNING id",
        (code,),
    )


JOB = (
    "INSERT INTO core.employee_job_records (employee_id, valid_during, legal_entity_id, "
    "location_id, department_id, designation_id, employment_type, change_reason) "
    "VALUES (%s, %s::daterange, %s, %s, %s, %s, 'full_time', 'hire')"
)


def job(conn: Conn, who: uuid.UUID, period: str, o: dict[str, uuid.UUID]) -> None:
    conn.execute(JOB, (who, period, o["entity"], o["location"], o["department"], o["designation"]))


def test_job_records_cannot_overlap_but_can_touch(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        o = org(api)
        who = employee(api, "E1")
        job(api, who, "[2024-01-01,2025-01-01)", o)
        job(api, who, "[2025-01-01,)", o)  # starts the day the last one ends: fine
    with (
        pytest.raises(psycopg.errors.ExclusionViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        job(api, who, "[2024-06-01,2024-07-01)", o)
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        job(api, employee(api, "E2"), "(,2024-01-01)", o)  # a record needs a start


def test_a_manager_cannot_be_the_employee_and_must_be_a_real_employee(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        o = org(api)
        who = employee(api, "E1")
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute(
            "INSERT INTO core.employee_job_records (employee_id, valid_during, legal_entity_id, "
            "location_id, department_id, designation_id, employment_type, change_reason, "
            "manager_employee_id) VALUES (%s, '[2024-01-01,)', %s, %s, %s, %s, 'full_time', "
            "'hire', %s)",
            (who, o["entity"], o["location"], o["department"], o["designation"], who),
        )
    with as_tenant(api, b.tenant_id, b.user_id):
        stranger = employee(api, "E1")
    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute(
            "INSERT INTO core.employee_job_records (employee_id, valid_during, legal_entity_id, "
            "location_id, department_id, designation_id, employment_type, change_reason, "
            "manager_employee_id) VALUES (%s, '[2024-01-01,)', %s, %s, %s, %s, 'full_time', "
            "'hire', %s)",
            (who, o["entity"], o["location"], o["department"], o["designation"], stranger),
        )


def test_the_current_view_shows_only_the_record_in_force(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        o = org(api)
        who = employee(api, "E1")
        job(api, who, "[2000-01-01,2001-01-01)", o)
        job(api, who, "[2001-01-01,2999-01-01)", o)
        job(api, who, "[2999-01-01,)", o)
        rows = api.execute(
            "SELECT lower(valid_during) FROM core.employee_job_records_current"
        ).fetchall()
    assert len(rows) == 1
    assert str(rows[0][0]) == "2001-01-01"
    with as_tenant(api, b.tenant_id, b.user_id):  # row-level security applies through the view
        assert api.execute("SELECT count(*) FROM core.employee_job_records_current").fetchone() == (
            0,
        )


def test_only_one_primary_bank_account_at_a_time(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    insert = (
        "INSERT INTO core.bank_accounts (employee_id, account_holder_name, account_number_enc, "
        "account_last4, account_bidx, ifsc, bank_name, is_primary, valid_during) "
        "VALUES (%s, 'A', '\\x00', '1234', '\\x00', 'HDFC0001234', 'HDFC', %s, %s::daterange)"
    )
    with as_tenant(api, a.tenant_id, a.user_id):
        who = employee(api, "E1")
        api.execute(insert, (who, True, "[2024-01-01,2025-01-01)"))
        api.execute(insert, (who, True, "[2025-01-01,)"))
        api.execute(insert, (who, False, "[2024-01-01,)"))  # secondary accounts may overlap
    with (
        pytest.raises(psycopg.errors.ExclusionViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute(insert, (who, True, "[2024-06-01,2024-07-01)"))
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute(insert.replace("'HDFC0001234'", "'hdfc1234'"), (who, False, "[2024-01-01,)"))


def test_nomination_shares_must_total_100_when_the_transaction_ends(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        who = employee(api, "E1")
        d1 = one(
            api,
            "INSERT INTO core.dependents (employee_id, name, relationship) "
            "VALUES (%s, 'X', 'spouse') RETURNING id",
            (who,),
        )
        d2 = one(
            api,
            "INSERT INTO core.dependents (employee_id, name, relationship) "
            "VALUES (%s, 'Y', 'child') RETURNING id",
            (who,),
        )
    nominate = (
        "INSERT INTO core.nominations (employee_id, scheme, dependent_id, share_percent) "
        "VALUES (%s, 'pf', %s, %s)"
    )
    with as_tenant(api, a.tenant_id, a.user_id):  # row by row inside a transaction is fine
        api.execute(nominate, (who, d1, 70))
        api.execute(nominate, (who, d2, 30))
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="total 100"),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute("UPDATE core.nominations SET share_percent = 40 WHERE dependent_id = %s", (d2,))
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute("DELETE FROM core.nominations WHERE dependent_id = %s", (d2,))


def test_identity_rules(connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]) -> None:
    a, b = two_tenants
    api = connect("pickwise_api")
    insert = (
        "INSERT INTO core.identity_documents (employee_id, doc_type, value_enc, value_last4, "
        "value_bidx) VALUES (%s, %s, %s, '234F', %s)"
    )
    with as_tenant(api, a.tenant_id, a.user_id):
        one_, two_ = employee(api, "E1"), employee(api, "E2")
        api.execute(insert, (one_, "pan", b"\x01", b"\x0a"))
    # The same blind index is a duplicate for another employee…
    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute(insert, (two_, "pan", b"\x02", b"\x0a"))
    # …but is free in another tenant, and for another document type.
    with as_tenant(api, a.tenant_id, a.user_id):
        api.execute(insert, (two_, "uan", b"\x02", b"\x0a"))
    with as_tenant(api, b.tenant_id, b.user_id):
        api.execute(insert, (employee(api, "E1"), "pan", b"\x03", b"\x0a"))
    # A PAN must come with its blind index; ciphertext and index go together.
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute(
            "INSERT INTO core.identity_documents (employee_id, doc_type, value_last4) "
            "VALUES (%s, 'pan', '234F')",
            (employee(api, "E3"),),
        )
    # Aadhaar kept as its last four digits has neither (no hash of the number).
    with as_tenant(api, a.tenant_id, a.user_id):
        api.execute(
            "INSERT INTO core.identity_documents (employee_id, doc_type, value_last4) "
            "VALUES (%s, 'aadhaar', '4321')",
            (two_,),
        )
    # One current document per type per employee.
    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute(insert, (one_, "pan", b"\x09", b"\x0b"))


def test_employee_checks(connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        who = employee(api, "E1")
        name = api.execute(
            "SELECT display_name FROM core.employees WHERE id = %s", (who,)
        ).fetchone()
        assert name == ("A",)
        api.execute("UPDATE core.employees SET last_name = 'Tester' WHERE id = %s", (who,))
        assert api.execute(
            "SELECT display_name FROM core.employees WHERE id = %s", (who,)
        ).fetchone() == ("A Tester",)
        api.execute("UPDATE core.employees SET preferred_name = 'Annie' WHERE id = %s", (who,))
        assert api.execute(
            "SELECT display_name FROM core.employees WHERE id = %s", (who,)
        ).fetchone() == ("Annie",)
    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        employee(api, "e1")  # codes are case-insensitive
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute("UPDATE core.employees SET date_of_exit = '2023-01-01' WHERE id = %s", (who,))
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute("UPDATE core.employees SET status = 'exited' WHERE id = %s", (who,))
