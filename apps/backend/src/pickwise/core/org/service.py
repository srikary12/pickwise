# SPDX-License-Identifier: AGPL-3.0-only
"""Org rules beyond plain CRUD: timezone validation, registration periods, department moves."""

import uuid
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import DATERANGE, Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.core.org import crud
from pickwise.core.org.crud import Spec
from pickwise.shared.errors import ConflictError, NotFoundError, UnprocessableError


def _names(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(","))


LEGAL_ENTITY = Spec(
    table="core.legal_entities",
    noun="legal entity",
    columns=_names(
        "id, name, legal_name, country_code, pan, tan, gstin, cin, registered_address, "
        "pf_establishment_code, esi_employer_code, archived_at, row_version"
    ),
    writable=_names(
        "name, legal_name, country_code, pan, tan, gstin, cin, registered_address, "
        "pf_establishment_code, esi_employer_code"
    ),
    order_by="lower(name)",
    jsonb=frozenset({"registered_address"}),
)
LOCATION = Spec(
    table="core.locations",
    noun="location",
    columns=_names(
        "id, legal_entity_id, code, name, address, state_code, city, pincode, timezone, "
        "latitude, longitude, geofence_radius_m, archived_at, row_version"
    ),
    writable=_names(
        "legal_entity_id, code, name, address, state_code, city, pincode, timezone, latitude, "
        "longitude, geofence_radius_m"
    ),
    order_by="lower(code)",
    jsonb=frozenset({"address"}),
    references={"legal_entity": "That legal entity doesn't exist."},
)
COST_CENTER = Spec(
    table="core.cost_centers",
    noun="cost centre",
    columns=_names("id, legal_entity_id, code, name, archived_at, row_version"),
    writable=_names("legal_entity_id, code, name"),
    order_by="lower(code)",
    references={"legal_entity": "That legal entity doesn't exist."},
)
DESIGNATION = Spec(
    table="core.designations",
    noun="designation",
    columns=_names("id, code, name, job_family, archived_at, row_version"),
    writable=_names("code, name, job_family"),
    order_by="lower(name)",
)
GRADE = Spec(
    table="core.grades",
    noun="grade",
    columns=_names("id, code, name, rank, ctc_min, ctc_max, archived_at, row_version"),
    writable=_names("code, name, rank, ctc_min, ctc_max"),
    order_by="rank, lower(code)",
)
DEPARTMENT = Spec(
    table="core.departments",
    noun="department",
    columns=_names(
        "id, code, name, parent_id, cost_center_id, path::text AS path, "
        "nlevel(path) - 1 AS depth, archived_at, row_version"
    ),
    writable=_names("code, name, parent_id, cost_center_id"),
    order_by="core.departments.path",
    references={
        "parent": "That parent department doesn't exist.",
        "cost_center": "That cost centre doesn't exist.",
    },
)
REGISTRATION_COLUMNS = (
    "id, legal_entity_id, registration_type, state_code, registration_no, "
    "lower(valid_during) AS valid_from, "
    "CASE WHEN upper_inf(valid_during) THEN NULL ELSE upper(valid_during) - 1 END AS valid_to, "
    "row_version"
)


def check_timezone(name: str | None) -> None:
    if name is None:
        return
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise UnprocessableError("Unknown timezone.", code="invalid_timezone") from None


# --- departments -----------------------------------------------------------------------------


async def _active_children(db: AsyncSession, department_id: uuid.UUID) -> int:
    count: int = (
        await db.execute(
            text(
                "SELECT count(*) FROM core.departments "
                "WHERE parent_id = :id AND archived_at IS NULL"
            ),
            {"id": department_id},
        )
    ).scalar_one()
    return count


async def archive_department(db: AsyncSession, department_id: uuid.UUID) -> dict[str, Any]:
    if await _active_children(db, department_id):
        raise ConflictError(
            "Archive or move this department's sub-departments first.", code="has_active_children"
        )
    return await crud.set_archived(db, DEPARTMENT, department_id, True)


async def unarchive_department(db: AsyncSession, department_id: uuid.UUID) -> dict[str, Any]:
    current = await crud.get_row(db, DEPARTMENT, department_id)
    if current["parent_id"] is not None:
        parent = await crud.get_row(db, DEPARTMENT, current["parent_id"])
        if parent["archived_at"] is not None:
            raise ConflictError("Restore the parent department first.", code="parent_archived")
    return await crud.set_archived(db, DEPARTMENT, department_id, False)


async def move_department(
    db: AsyncSession, department_id: uuid.UUID, parent_id: uuid.UUID | None, row_version: int
) -> dict[str, Any]:
    """Re-parent a department. The path trigger rewrites the subtree and rejects cycles."""
    current = await crud.get_row(db, DEPARTMENT, department_id)
    if current["row_version"] != row_version:
        raise ConflictError(
            "Someone else changed this department. Reload and try again.",
            code="stale_row_version",
        )
    if parent_id is not None:
        parent = await crud.get_row(db, DEPARTMENT, parent_id)
        if parent["archived_at"] is not None:
            raise UnprocessableError(
                "Can't move a department under an archived one.", code="parent_archived"
            )
    try:
        return await crud.update_row(
            db, DEPARTMENT, department_id, {"parent_id": parent_id}, row_version
        )
    except UnprocessableError as exc:
        if exc.code == "invalid":
            raise UnprocessableError(
                "A department can't be moved under itself or one of its own sub-departments.",
                code="department_cycle",
            ) from None
        raise


# --- registrations ---------------------------------------------------------------------------


def _period(valid_from: date, valid_to: date | None) -> Range[date]:
    # A daterange is [from, to + 1): the last day is inclusive for people, exclusive in Postgres.
    upper = None if valid_to is None else valid_to + timedelta(days=1)
    return Range(valid_from, upper, bounds="[)")


async def list_registrations(db: AsyncSession, legal_entity_id: uuid.UUID) -> list[dict[str, Any]]:
    await crud.get_row(db, LEGAL_ENTITY, legal_entity_id)
    rows = (
        await db.execute(
            text(
                f"SELECT {REGISTRATION_COLUMNS} FROM core.legal_entity_registrations "  # noqa: S608
                "WHERE legal_entity_id = :e ORDER BY registration_type, state_code, valid_during"
            ),
            {"e": legal_entity_id},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def _write_registration(
    db: AsyncSession, statement: str, params: dict[str, Any]
) -> dict[str, Any]:
    try:
        async with db.begin_nested():
            row = (
                (
                    await db.execute(
                        text(statement).bindparams(bindparam("p", type_=DATERANGE)), params
                    )
                )
                .mappings()
                .one_or_none()
            )
    except IntegrityError as exc:
        error = crud.pg_error(exc)
        if error.sqlstate == crud.EXCLUSION_VIOLATION:
            raise ConflictError(
                "That registration overlaps another one for the same state and type.",
                code="registration_overlap",
            ) from exc
        raise
    if row is None:
        raise ConflictError(
            "Someone else changed this registration. Reload and try again.",
            code="stale_row_version",
        )
    return dict(row)


async def create_registration(
    db: AsyncSession, legal_entity_id: uuid.UUID, values: dict[str, Any]
) -> dict[str, Any]:
    await crud.get_row(db, LEGAL_ENTITY, legal_entity_id)
    return await _write_registration(
        db,
        "INSERT INTO core.legal_entity_registrations (legal_entity_id, registration_type, "  # noqa: S608
        "  state_code, registration_no, valid_during) "
        "VALUES (:e, :t, :s, :n, :p) "
        f"RETURNING {REGISTRATION_COLUMNS}",
        {
            "e": legal_entity_id,
            "t": values["registration_type"],
            "s": values["state_code"],
            "n": values["registration_no"],
            "p": _period(values["valid_from"], values["valid_to"]),
        },
    )


async def update_registration(
    db: AsyncSession,
    legal_entity_id: uuid.UUID,
    registration_id: uuid.UUID,
    values: dict[str, Any],
    row_version: int,
) -> dict[str, Any]:
    exists = (
        await db.execute(
            text(
                "SELECT 1 FROM core.legal_entity_registrations "
                "WHERE id = :id AND legal_entity_id = :e"
            ),
            {"id": registration_id, "e": legal_entity_id},
        )
    ).first()
    if exists is None:
        raise NotFoundError("Registration not found.")
    return await _write_registration(
        db,
        "UPDATE core.legal_entity_registrations SET registration_type = :t, state_code = :s, "  # noqa: S608
        "  registration_no = :n, valid_during = :p "
        "WHERE id = :id AND row_version = :v "
        f"RETURNING {REGISTRATION_COLUMNS}",
        {
            "id": registration_id,
            "v": row_version,
            "t": values["registration_type"],
            "s": values["state_code"],
            "n": values["registration_no"],
            "p": _period(values["valid_from"], values["valid_to"]),
        },
    )


async def delete_registration(
    db: AsyncSession, legal_entity_id: uuid.UUID, registration_id: uuid.UUID
) -> None:
    result = await db.execute(
        text("DELETE FROM core.legal_entity_registrations WHERE id = :id AND legal_entity_id = :e"),
        {"id": registration_id, "e": legal_entity_id},
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFoundError("Registration not found.")
