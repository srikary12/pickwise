# SPDX-License-Identifier: AGPL-3.0-only
"""Employees: listing within the caller's data scope, creating, editing and onboarding status."""

import base64
import datetime
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.core import hierarchy
from pickwise.core.crud import CHECK_VIOLATION, UNIQUE_VIOLATION, pg_error
from pickwise.core.job_records import service as jobs
from pickwise.core.tables import current_jobs, departments, designations, employees, locations
from pickwise.platform.custom_fields.service import validate_custom_fields
from pickwise.platform.events.service import emit_event
from pickwise.platform.rbac.principal import Principal
from pickwise.platform.scopes import DataScope, ScopeTarget, scope_filter
from pickwise.shared.errors import ConflictError, NotFoundError, UnprocessableError

manager = employees.alias("manager")
LIVE = ("active", "notice_period", "leave_of_absence")


@dataclass(frozen=True, slots=True)
class Filters:
    q: str | None = None
    status: str | None = None
    department_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    legal_entity_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None


def like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def visible(scopes: tuple[DataScope, ...], principal: Principal) -> Any:
    return scope_filter(scopes, principal, ScopeTarget(employee_column=employees.c.id))


def _query() -> Select[Any]:
    return select(
        employees,
        (employees.c.membership_id.is_not(None)).label("has_login"),
        current_jobs.c.legal_entity_id,
        current_jobs.c.location_id,
        locations.c.name.label("location_name"),
        current_jobs.c.department_id,
        departments.c.name.label("department_name"),
        current_jobs.c.designation_id,
        designations.c.name.label("designation_name"),
        current_jobs.c.manager_employee_id,
        manager.c.display_name.label("manager_name"),
        current_jobs.c.employment_type,
    ).select_from(
        employees.outerjoin(current_jobs, current_jobs.c.employee_id == employees.c.id)
        .outerjoin(locations, locations.c.id == current_jobs.c.location_id)
        .outerjoin(departments, departments.c.id == current_jobs.c.department_id)
        .outerjoin(designations, designations.c.id == current_jobs.c.designation_id)
        .outerjoin(manager, manager.c.id == current_jobs.c.manager_employee_id)
    )


def _apply(query: Select[Any], f: Filters) -> Select[Any]:
    if f.q:
        pattern = like_pattern(f.q.strip())
        query = query.where(
            or_(
                employees.c.display_name.ilike(pattern, escape="\\"),
                employees.c.employee_code.ilike(pattern, escape="\\"),
                employees.c.work_email.ilike(pattern, escape="\\"),
            )
        )
    if f.status:
        query = query.where(employees.c.status == f.status)
    if f.department_id:
        query = query.where(current_jobs.c.department_id == f.department_id)
    if f.location_id:
        query = query.where(current_jobs.c.location_id == f.location_id)
    if f.legal_entity_id:
        query = query.where(current_jobs.c.legal_entity_id == f.legal_entity_id)
    if f.manager_id:
        query = query.where(current_jobs.c.manager_employee_id == f.manager_id)
    return query


def present(row: Any) -> dict[str, Any]:
    """A query row as the API's EmployeeOut shape."""
    r = row._mapping
    job = None
    if r["legal_entity_id"] is not None:
        job = {
            k: r[k]
            for k in (
                "legal_entity_id",
                "location_id",
                "location_name",
                "department_id",
                "department_name",
                "designation_id",
                "designation_name",
                "manager_employee_id",
                "manager_name",
                "employment_type",
            )
        }
    return {
        **{
            k: r[k]
            for k in (
                "id",
                "employee_code",
                "status",
                "display_name",
                "first_name",
                "middle_name",
                "last_name",
                "preferred_name",
                "work_email",
                "work_phone",
                "date_of_joining",
                "original_hire_date",
                "probation_end_date",
                "confirmation_date",
                "date_of_exit",
                "photo_file_id",
                "custom_fields",
                "row_version",
            )
        },
        "employee_code": str(r["employee_code"]),
        "work_email": None if r["work_email"] is None else str(r["work_email"]),
        "has_login": r["has_login"],
        "job": job,
    }


def encode_cursor(name: str, employee_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{name}\x00{employee_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        name, _, ident = raw.partition("\x00")
        return name, uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnprocessableError("That cursor isn't valid.", code="invalid_cursor") from exc


async def page(
    db: AsyncSession,
    principal: Principal,
    scopes: tuple[DataScope, ...],
    filters: Filters,
    *,
    cursor: str | None,
    limit: int,
    only_live: bool = False,
) -> tuple[list[Any], str | None]:
    sort_key = func.lower(employees.c.display_name)
    query = _apply(_query(), filters).where(visible(scopes, principal))
    if only_live:
        query = query.where(employees.c.status.in_(LIVE))
    if cursor:
        name, ident = decode_cursor(cursor)
        query = query.where(or_(sort_key > name, and_(sort_key == name, employees.c.id > ident)))
    rows = (await db.execute(query.order_by(sort_key, employees.c.id).limit(limit + 1))).all()
    more = rows[limit:]
    rows = rows[:limit]
    next_cursor = None
    if more and rows:
        last = rows[-1]._mapping
        next_cursor = encode_cursor(str(last["display_name"]).lower(), last["id"])
    return rows, next_cursor


async def get(
    db: AsyncSession,
    principal: Principal,
    scopes: tuple[DataScope, ...],
    employee_id: uuid.UUID,
) -> Any:
    """One employee the caller may see; anyone else is a 404, not a 403 (no existence leak)."""
    row = (
        await db.execute(_query().where(employees.c.id == employee_id, visible(scopes, principal)))
    ).first()
    if row is None:
        raise NotFoundError("Employee not found.")
    return row


async def employee_of_membership(db: AsyncSession, membership_id: uuid.UUID | None) -> uuid.UUID:
    found: uuid.UUID | None = (
        await db.execute(
            text("SELECT id FROM core.employees WHERE membership_id = :m"), {"m": membership_id}
        )
    ).scalar_one_or_none()
    if found is None:
        raise NotFoundError("You don't have an employee record.", code="no_employee_record")
    return found


# --- codes -----------------------------------------------------------------------------------


async def _next_code(db: AsyncSession) -> str:
    await db.execute(
        text(
            "INSERT INTO platform.number_sequences (tenant_id, key, prefix) "
            "VALUES (platform.current_tenant_id(), 'employee_code', 'EMP') ON CONFLICT DO NOTHING"
        )
    )
    for _ in range(50):  # skip codes an import or a manual entry already took
        row = (
            await db.execute(
                text(
                    "UPDATE platform.number_sequences SET next_value = next_value + 1 "
                    "WHERE key = 'employee_code' RETURNING prefix, next_value - 1, padding"
                )
            )
        ).one()
        code = f"{row[0]}{str(row[1]).zfill(int(row[2]))}"
        taken = (
            await db.execute(
                text("SELECT 1 FROM core.employees WHERE employee_code = :c"), {"c": code}
            )
        ).first()
        if taken is None:
            return code
    raise ConflictError("Couldn't allocate an employee code. Try again.", code="code_exhausted")


# --- create and edit ---------------------------------------------------------------------------

_EDITABLE = (
    "first_name",
    "middle_name",
    "last_name",
    "preferred_name",
    "work_email",
    "work_phone",
    "original_hire_date",
    "probation_end_date",
    "confirmation_date",
)


def _integrity(exc: IntegrityError) -> Exception:
    error = pg_error(exc)
    if error.sqlstate == UNIQUE_VIOLATION:
        if error.constraint and "work_email" in error.constraint:
            return ConflictError("Another employee has that work email.", code="duplicate_email")
        return ConflictError("That employee already exists.", code="duplicate")
    if error.sqlstate == CHECK_VIOLATION:
        return UnprocessableError("Check the dates; they don't add up.", code="invalid_dates")
    return exc


async def create(
    db: AsyncSession,
    values: dict[str, Any],
    job: dict[str, Any],
    *,
    can_edit_sensitive: bool,
) -> uuid.UUID:
    custom = await validate_custom_fields(
        db, "employee", values.pop("custom_fields", {}), can_edit_sensitive=can_edit_sensitive
    )
    code = await _next_code(db)
    joined: datetime.date = values.pop("date_of_joining")
    columns = [*(c for c in _EDITABLE if c in values), "date_of_joining"]
    params = {**{c: values.get(c) for c in _EDITABLE}, "date_of_joining": joined, "code": code}
    from sqlalchemy import bindparam
    from sqlalchemy.dialects.postgresql import JSONB

    statement = text(
        f"INSERT INTO core.employees (employee_code, custom_fields, {', '.join(columns)}) "  # noqa: S608
        f"VALUES (:code, :custom, {', '.join(':' + c for c in columns)}) RETURNING id"
    ).bindparams(bindparam("custom", type_=JSONB))
    try:
        async with db.begin_nested():
            employee_id: uuid.UUID = (
                await db.execute(statement, {**params, "custom": custom})
            ).scalar_one()
    except IntegrityError as exc:
        raise _integrity(exc) from exc
    await hierarchy.add_employee(db, employee_id)
    await jobs.create_first(db, employee_id, joined, job)
    await emit_event(
        db,
        aggregate_type="employee",
        aggregate_id=employee_id,
        event_type="core.employee.created",
        payload={"employee_id": str(employee_id)},
    )
    return employee_id


async def update(
    db: AsyncSession,
    employee_id: uuid.UUID,
    values: dict[str, Any],
    row_version: int,
    *,
    can_edit_sensitive: bool,
) -> None:
    existing = (
        await db.execute(
            text("SELECT custom_fields, status FROM core.employees WHERE id = :id"),
            {"id": employee_id},
        )
    ).one_or_none()
    if existing is None:
        raise NotFoundError("Employee not found.")
    from sqlalchemy import bindparam
    from sqlalchemy.dialects.postgresql import JSONB

    custom = await validate_custom_fields(
        db,
        "employee",
        values.get("custom_fields", {}),
        existing=existing[0],
        can_edit_sensitive=can_edit_sensitive,
    )
    sets = ", ".join(f"{c} = :{c}" for c in _EDITABLE)
    statement = text(
        f"UPDATE core.employees SET {sets}, custom_fields = :custom "  # noqa: S608
        "WHERE id = :id AND row_version = :v RETURNING id"
    ).bindparams(bindparam("custom", type_=JSONB))
    try:
        async with db.begin_nested():
            updated = (
                await db.execute(
                    statement,
                    {
                        **{c: values.get(c) for c in _EDITABLE},
                        "custom": custom,
                        "id": employee_id,
                        "v": row_version,
                    },
                )
            ).first()
    except IntegrityError as exc:
        raise _integrity(exc) from exc
    if updated is None:
        raise ConflictError(
            "Someone else changed this employee. Reload and try again.", code="stale_row_version"
        )


_TRANSITIONS = {
    ("draft", "pre_boarding"),
    ("draft", "active"),
    ("pre_boarding", "active"),
}


async def set_status(db: AsyncSession, employee_id: uuid.UUID, to: str, row_version: int) -> None:
    row = (
        await db.execute(
            text(
                "SELECT status, date_of_joining, row_version FROM core.employees "
                "WHERE id = :id FOR UPDATE"
            ),
            {"id": employee_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Employee not found.")
    if row[2] != row_version:
        raise ConflictError(
            "Someone else changed this employee. Reload and try again.", code="stale_row_version"
        )
    if (row[0], to) not in _TRANSITIONS:
        raise UnprocessableError(
            f"An employee can't go from {row[0]} to {to}.", code="invalid_transition"
        )
    if to == "active":
        today: datetime.date = (await db.execute(text("SELECT core.today()"))).scalar_one()
        if row[1] > today:
            raise UnprocessableError(
                "They can't be made active before their joining date.", code="before_joining"
            )
    await db.execute(
        text("UPDATE core.employees SET status = :s WHERE id = :id"), {"s": to, "id": employee_id}
    )
    await emit_event(
        db,
        aggregate_type="employee",
        aggregate_id=employee_id,
        event_type="core.employee.status_changed",
        payload={"employee_id": str(employee_id), "from": row[0], "to": to},
    )


async def link_user(
    db: AsyncSession, employee_id: uuid.UUID, membership_id: uuid.UUID | None, row_version: int
) -> None:
    if membership_id is not None:
        status = (
            await db.execute(
                text("SELECT status FROM platform.memberships WHERE id = :m"), {"m": membership_id}
            )
        ).scalar_one_or_none()
        if status not in ("invited", "active"):
            raise UnprocessableError("That person can't sign in.", code="unknown_membership")
    try:
        async with db.begin_nested():
            updated = (
                await db.execute(
                    text(
                        "UPDATE core.employees SET membership_id = :m "
                        "WHERE id = :id AND row_version = :v RETURNING id"
                    ),
                    {"m": membership_id, "id": employee_id, "v": row_version},
                )
            ).first()
    except IntegrityError as exc:
        if pg_error(exc).sqlstate == UNIQUE_VIOLATION:
            raise ConflictError(
                "That person is already linked to another employee.", code="membership_taken"
            ) from exc
        raise
    if updated is None:
        exists = (
            await db.execute(
                text("SELECT 1 FROM core.employees WHERE id = :id"), {"id": employee_id}
            )
        ).first()
        if exists is None:
            raise NotFoundError("Employee not found.")
        raise ConflictError(
            "Someone else changed this employee. Reload and try again.", code="stale_row_version"
        )
    await hierarchy.sync_manager_roles(db)
