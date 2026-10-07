# SPDX-License-Identifier: AGPL-3.0-only
"""An employee's personal data, addresses, contacts, family, education, experience and documents.

The child tables share one shape (rows owned by an employee, edited with ``row_version``), so
the plain create/update/delete go through ``core.crud``'s specs; what is special (the 1:1 personal
record, effective-dated addresses, replace-the-set nominations, document files) lives here.
"""

import datetime
import uuid
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import DATERANGE, Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.core import crud
from pickwise.core.crud import Spec
from pickwise.platform.auth.dependencies import Authorized
from pickwise.platform.files import service as files
from pickwise.shared.errors import ConflictError, NotFoundError, UnprocessableError


def names(columns: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in columns.split(","))


EMERGENCY = Spec(
    table="core.emergency_contacts",
    noun="emergency contact",
    columns=names("id, employee_id, name, relationship, phone, email, is_primary, row_version"),
    writable=names("employee_id, name, relationship, phone, email, is_primary"),
    order_by="is_primary DESC, lower(name)",
)
DEPENDENT = Spec(
    table="core.dependents",
    noun="dependent",
    columns=names("id, employee_id, name, relationship, date_of_birth, gender, row_version"),
    writable=names("employee_id, name, relationship, date_of_birth, gender"),
    order_by="lower(name)",
)
EDUCATION = Spec(
    table="core.education_history",
    noun="education entry",
    columns=names(
        "id, employee_id, institution, degree, field_of_study, start_year, end_year, row_version"
    ),
    writable=names("employee_id, institution, degree, field_of_study, start_year, end_year"),
    order_by="coalesce(end_year, 9999) DESC, lower(institution)",
)
EXPERIENCE = Spec(
    table="core.employment_history",
    noun="experience entry",
    columns=names("id, employee_id, employer, title, from_date, to_date, row_version"),
    writable=names("employee_id, employer, title, from_date, to_date"),
    order_by="coalesce(to_date, DATE '9999-12-31') DESC, lower(employer)",
)
DOCUMENT = Spec(
    table="core.employee_documents",
    noun="document",
    columns=names(
        "id, employee_id, category, file_id, title, visible_to_employee, expires_on, row_version"
    ),
    writable=names("employee_id, category, file_id, title, visible_to_employee, expires_on"),
    order_by="lower(title)",
)


async def list_for(
    db: AsyncSession, spec: Spec, employee_id: uuid.UUID, *, where: str = "true"
) -> list[dict[str, Any]]:
    return await crud.list_rows(
        db, spec, include_archived=True, where=f"employee_id = :e AND ({where})", e=employee_id
    )


async def owned(
    db: AsyncSession, spec: Spec, employee_id: uuid.UUID, row_id: uuid.UUID
) -> dict[str, Any]:
    """The row, if it belongs to this employee (anything else is a 404)."""
    row = await crud.get_row(db, spec, row_id)
    if row["employee_id"] != employee_id:
        raise NotFoundError(f"{spec.noun.capitalize()} not found.")
    return row


async def add(
    db: AsyncSession, spec: Spec, employee_id: uuid.UUID, values: dict[str, Any]
) -> dict[str, Any]:
    return await crud.insert_row(db, spec, {**values, "employee_id": employee_id})


async def edit(
    db: AsyncSession,
    spec: Spec,
    employee_id: uuid.UUID,
    row_id: uuid.UUID,
    values: dict[str, Any],
    row_version: int,
) -> dict[str, Any]:
    await owned(db, spec, employee_id, row_id)
    return await crud.update_row(db, spec, row_id, values, row_version)


async def remove(db: AsyncSession, spec: Spec, employee_id: uuid.UUID, row_id: uuid.UUID) -> None:
    await owned(db, spec, employee_id, row_id)
    try:
        async with db.begin_nested():
            await db.execute(text(f"DELETE FROM {spec.table} WHERE id = :id"), {"id": row_id})  # noqa: S608
    except IntegrityError as exc:
        if crud.pg_error(exc).sqlstate == crud.FOREIGN_KEY_VIOLATION:
            raise ConflictError(f"That {spec.noun} is still in use.", code="in_use") from exc
        raise


# --- emergency contacts: one primary ---------------------------------------------------------


async def add_contact(
    db: AsyncSession, employee_id: uuid.UUID, values: dict[str, Any]
) -> dict[str, Any]:
    if values.get("is_primary"):
        await _clear_primary(db, employee_id)
    return await add(db, EMERGENCY, employee_id, values)


async def edit_contact(
    db: AsyncSession,
    employee_id: uuid.UUID,
    contact_id: uuid.UUID,
    values: dict[str, Any],
    row_version: int,
) -> dict[str, Any]:
    await owned(db, EMERGENCY, employee_id, contact_id)
    if values.get("is_primary"):
        await _clear_primary(db, employee_id, except_id=contact_id)
    return await crud.update_row(db, EMERGENCY, contact_id, values, row_version)


async def _clear_primary(
    db: AsyncSession, employee_id: uuid.UUID, except_id: uuid.UUID | None = None
) -> None:
    await db.execute(
        text(
            "UPDATE core.emergency_contacts SET is_primary = false "
            "WHERE employee_id = :e AND is_primary AND (CAST(:x AS uuid) IS NULL OR id <> :x)"
        ),
        {"e": employee_id, "x": except_id},
    )


# --- personal -------------------------------------------------------------------------------

_PERSONAL = (
    "date_of_birth",
    "gender",
    "marital_status",
    "blood_group",
    "nationality",
    "father_or_spouse_name",
    "is_person_with_disability",
)


async def get_personal(db: AsyncSession, employee_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                text(
                    "SELECT e.id AS employee_id, e.personal_email::text AS personal_email, "
                    "  e.personal_phone, p.date_of_birth, p.gender, p.marital_status, "
                    "  p.blood_group, p.nationality, p.father_or_spouse_name, "
                    "  coalesce(p.is_person_with_disability, false) AS is_person_with_disability, "
                    "  p.row_version "
                    "FROM core.employees e LEFT JOIN core.employee_personal p "
                    "  ON p.tenant_id = e.tenant_id AND p.employee_id = e.id WHERE e.id = :e"
                ),
                {"e": employee_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFoundError("Employee not found.")
    return dict(row)


async def save_personal(
    db: AsyncSession, employee_id: uuid.UUID, values: dict[str, Any], row_version: int | None
) -> dict[str, Any]:
    current = await get_personal(db, employee_id)
    await set_contact_details(db, employee_id, values)
    params = {c: values.get(c) for c in _PERSONAL}
    if current["row_version"] is None:
        if row_version is not None:
            raise ConflictError("Reload and try again.", code="stale_row_version")
        await db.execute(
            text(
                f"INSERT INTO core.employee_personal (employee_id, {', '.join(_PERSONAL)}) "  # noqa: S608
                f"VALUES (:employee_id, {', '.join(':' + c for c in _PERSONAL)}) "
                "ON CONFLICT (tenant_id, employee_id) DO NOTHING"
            ),
            {**params, "employee_id": employee_id},
        )
    else:
        if row_version != current["row_version"]:
            raise ConflictError(
                "Someone else changed this. Reload and try again.", code="stale_row_version"
            )
        sets = ", ".join(f"{c} = :{c}" for c in _PERSONAL)
        await db.execute(
            text(f"UPDATE core.employee_personal SET {sets} WHERE employee_id = :employee_id"),  # noqa: S608
            {**params, "employee_id": employee_id},
        )
    return await get_personal(db, employee_id)


async def set_contact_details(
    db: AsyncSession, employee_id: uuid.UUID, values: dict[str, Any]
) -> None:
    await db.execute(
        text("UPDATE core.employees SET personal_email = :pe, personal_phone = :pp WHERE id = :e"),
        {"pe": values.get("personal_email"), "pp": values.get("personal_phone"), "e": employee_id},
    )


# --- addresses (effective-dated) -------------------------------------------------------------

_ADDRESS_COLUMNS = (
    "id, address_type, line1, line2, city, state_code, pincode, country_code, "
    "lower(valid_during) AS valid_from, "
    "CASE WHEN upper_inf(valid_during) THEN NULL ELSE upper(valid_during) - 1 END AS valid_to, "
    "row_version"
)
_RANGE = bindparam("period", type_=DATERANGE)


async def list_addresses(
    db: AsyncSession, employee_id: uuid.UUID, *, current_only: bool = False
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                f"SELECT {_ADDRESS_COLUMNS} FROM core.employee_addresses "  # noqa: S608
                "WHERE employee_id = :e AND (NOT :cur OR valid_during @> core.today()) "
                "ORDER BY address_type, valid_during DESC"
            ),
            {"e": employee_id, "cur": current_only},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def set_address(
    db: AsyncSession,
    employee_id: uuid.UUID,
    address_type: str,
    values: dict[str, Any],
    valid_from: datetime.date | None,
) -> dict[str, Any]:
    today: datetime.date = (await db.execute(text("SELECT core.today()"))).scalar_one()
    start = valid_from or today
    # The address in force on the start date ends the day before; later ones are replaced.
    previous = (
        await db.execute(
            text(
                "SELECT id, lower(valid_during) FROM core.employee_addresses "
                "WHERE employee_id = :e AND address_type = :t AND valid_during @> CAST(:d AS date)"
            ),
            {"e": employee_id, "t": address_type, "d": start},
        )
    ).first()
    if previous is not None:
        if previous[1] >= start:
            await db.execute(
                text("DELETE FROM core.employee_addresses WHERE id = :id"), {"id": previous[0]}
            )
        else:
            await db.execute(
                text(
                    "UPDATE core.employee_addresses SET valid_during = :period WHERE id = :id"
                ).bindparams(_RANGE),
                {"id": previous[0], "period": Range(previous[1], start, bounds="[)")},
            )
    await db.execute(
        text(
            "DELETE FROM core.employee_addresses "
            "WHERE employee_id = :e AND address_type = :t AND lower(valid_during) > :d"
        ),
        {"e": employee_id, "t": address_type, "d": start},
    )
    row = (
        (
            await db.execute(
                text(
                    "INSERT INTO core.employee_addresses (employee_id, address_type, line1, line2, "  # noqa: S608
                    "  city, state_code, pincode, country_code, valid_during) "
                    "VALUES (:e, :t, :line1, :line2, :city, :state_code, :pincode, :country_code, "
                    "  :period) "
                    f"RETURNING {_ADDRESS_COLUMNS}"
                ).bindparams(_RANGE),
                {
                    "e": employee_id,
                    "t": address_type,
                    "period": Range(start, None, bounds="[)"),
                    **{
                        k: values.get(k)
                        for k in ("line1", "line2", "city", "state_code", "pincode", "country_code")
                    },
                },
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


# --- nominations -----------------------------------------------------------------------------


async def list_nominations(db: AsyncSession, employee_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT scheme, dependent_id, share_percent FROM core.nominations "
                "WHERE employee_id = :e ORDER BY scheme, share_percent DESC"
            ),
            {"e": employee_id},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def set_nominations(
    db: AsyncSession, employee_id: uuid.UUID, scheme: str, shares: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    dependents = {
        r[0]
        for r in (
            await db.execute(
                text("SELECT id FROM core.dependents WHERE employee_id = :e"), {"e": employee_id}
            )
        ).all()
    }
    for share in shares:
        if share["dependent_id"] not in dependents:
            raise UnprocessableError(
                "Nominate people from this employee's dependents.", code="unknown_dependent"
            )
    await db.execute(
        text("DELETE FROM core.nominations WHERE employee_id = :e AND scheme = :s"),
        {"e": employee_id, "s": scheme},
    )
    for share in shares:
        await db.execute(
            text(
                "INSERT INTO core.nominations (employee_id, scheme, dependent_id, share_percent) "
                "VALUES (:e, :s, :d, :p)"
            ),
            {
                "e": employee_id,
                "s": scheme,
                "d": share["dependent_id"],
                "p": share["share_percent"],
            },
        )
    return await list_nominations(db, employee_id)


# --- documents (files) -----------------------------------------------------------------------

DOCUMENT_OWNER_TYPE = "employee_document"


async def check_document_file(
    db: AsyncSession, auth: Authorized, employee_id: uuid.UUID, file_id: uuid.UUID
) -> None:
    """The file must be the caller's own upload for this employee, and scanned clean."""
    file = await files.get_file(db, file_id)
    if file is None or file.created_by != auth.principal.user_id:
        raise NotFoundError("File not found.")
    if file.owner_entity_type != DOCUMENT_OWNER_TYPE or file.owner_entity_id != employee_id:
        raise UnprocessableError(
            "That file wasn't uploaded for this employee's documents.", code="invalid_file"
        )
    if file.scan_status == "pending":
        raise ConflictError("The file is still being checked.", code="file_not_ready")
    if file.scan_status != "clean" or file.purged_at is not None:
        raise UnprocessableError("That file isn't available.", code="file_unavailable")
