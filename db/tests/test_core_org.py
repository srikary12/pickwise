# SPDX-License-Identifier: AGPL-3.0-only
"""core org structure: the department path trigger and the registration overlap constraint."""

import uuid

import psycopg
import pytest
from conftest import Conn, Connect, SeededTenant, as_tenant

pytestmark = pytest.mark.db


def label(department_id: uuid.UUID) -> str:
    return str(department_id).replace("-", "")


def add_department(conn: Conn, code: str, parent: uuid.UUID | None = None) -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO core.departments (code, name, parent_id) VALUES (%s, %s, %s) RETURNING id",
        (code, code, parent),
    ).fetchone()
    assert row is not None
    assert isinstance(row[0], uuid.UUID)
    return row[0]


def path_of(conn: Conn, department_id: uuid.UUID) -> str:
    row = conn.execute(
        "SELECT path::text FROM core.departments WHERE id = %s", (department_id,)
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_paths_follow_the_tree_and_a_move_rewrites_the_subtree(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        root = add_department(api, "ENG")
        platform_ = add_department(api, "PLAT", root)
        infra = add_department(api, "INFRA", platform_)
        sales = add_department(api, "SALES")

        assert path_of(api, root) == label(root)
        assert path_of(api, infra) == f"{label(root)}.{label(platform_)}.{label(infra)}"

        # Move PLAT (with INFRA below it) under SALES.
        api.execute("UPDATE core.departments SET parent_id = %s WHERE id = %s", (sales, platform_))
        assert path_of(api, platform_) == f"{label(sales)}.{label(platform_)}"
        assert path_of(api, infra) == f"{label(sales)}.{label(platform_)}.{label(infra)}"
        assert path_of(api, root) == label(root)

        # To the top level.
        api.execute("UPDATE core.departments SET parent_id = NULL WHERE id = %s", (platform_,))
        assert path_of(api, infra) == f"{label(platform_)}.{label(infra)}"


def test_a_move_under_itself_or_a_descendant_is_rejected(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        root = add_department(api, "R")
        child = add_department(api, "C", root)
        grandchild = add_department(api, "G", child)
    for target in (root, child, grandchild):
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="under itself"),
            as_tenant(api, a.tenant_id, a.user_id),
        ):
            api.execute("UPDATE core.departments SET parent_id = %s WHERE id = %s", (target, root))
    with as_tenant(api, a.tenant_id, a.user_id):
        assert path_of(api, grandchild).startswith(label(root))


def test_the_path_cannot_be_written_directly(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        department = add_department(api, "X")
    with (
        pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="maintained by trigger"),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute("UPDATE core.departments SET path = 'a.b' WHERE id = %s", (department,))


def test_a_parent_in_another_tenant_is_refused(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, b.tenant_id, b.user_id):
        theirs = add_department(api, "B1")
    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        add_department(api, "A1", theirs)


def test_registrations_cannot_overlap_for_the_same_state_and_type(
    connect: Connect, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = two_tenants
    api = connect("pickwise_api")
    with as_tenant(api, a.tenant_id, a.user_id):
        entity = api.execute(
            "INSERT INTO core.legal_entities (name, legal_name) VALUES ('E', 'E Pvt Ltd') "
            "RETURNING id"
        ).fetchone()
    assert entity is not None
    insert = (
        "INSERT INTO core.legal_entity_registrations "
        "(legal_entity_id, registration_type, state_code, registration_no, valid_during) "
        "VALUES (%s, %s, %s, 'N', %s::daterange)"
    )
    with as_tenant(api, a.tenant_id, a.user_id):
        api.execute(insert, (entity[0], "PT", "IN-KA", "[2025-04-01,2026-04-01)"))
        # Adjacent periods, another state and another type are all fine.
        api.execute(insert, (entity[0], "PT", "IN-KA", "[2026-04-01,)"))
        api.execute(insert, (entity[0], "PT", "IN-TG", "[2025-04-01,)"))
        api.execute(insert, (entity[0], "LWF", "IN-KA", "[2025-04-01,)"))
    with (
        pytest.raises(psycopg.errors.ExclusionViolation),
        as_tenant(api, a.tenant_id, a.user_id),
    ):
        api.execute(insert, (entity[0], "PT", "IN-KA", "[2025-12-01,2026-06-01)"))
