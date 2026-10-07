# SPDX-License-Identifier: AGPL-3.0-only
"""Effective-dated job records (CLAUDE.md rule 6, ADR 0023).

An employee's job (entity, location, department, designation, grade, cost centre, manager,
employment type) is a series of records whose periods never overlap and never leave a gap.
History is never overwritten:

- ``change`` closes the record in force on the effective date and opens a new one from that
  date, which runs until the next record starts (or open-ended). Future-dated changes are just
  records that start later; they take effect, for the reporting tree, when their day comes.
- ``correct`` fixes a mistake in a record in place (the audit trail keeps what it said) and can
  move the date a record starts, adjusting its neighbour so there is still no gap.
- A manager change keeps the reporting closure table exact in the same transaction.
"""

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import DATERANGE, Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.core import hierarchy
from pickwise.core.crud import EXCLUSION_VIOLATION, pg_error
from pickwise.platform.events.service import emit_event
from pickwise.shared.errors import ConflictError, NotFoundError, UnprocessableError

FIELDS = (
    "legal_entity_id",
    "location_id",
    "department_id",
    "designation_id",
    "grade_id",
    "cost_center_id",
    "manager_employee_id",
    "employment_type",
)
OPTIONAL = frozenset({"grade_id", "cost_center_id", "manager_employee_id"})
# Reference columns and the table they must point at (an active row).
_REFERENCES = {
    "legal_entity_id": ("core.legal_entities", "legal entity"),
    "location_id": ("core.locations", "location"),
    "department_id": ("core.departments", "department"),
    "designation_id": ("core.designations", "designation"),
    "grade_id": ("core.grades", "grade"),
    "cost_center_id": ("core.cost_centers", "cost centre"),
}
_COLUMNS = (
    "id, employee_id, lower(valid_during) AS valid_from, "
    "CASE WHEN upper_inf(valid_during) THEN NULL ELSE upper(valid_during) - 1 END AS valid_to, "
    "legal_entity_id, location_id, department_id, designation_id, grade_id, cost_center_id, "
    "manager_employee_id, employment_type, change_reason, approval_request_id, notes, "
    "row_version, valid_during @> core.today() AS is_current"
)
_RANGE = bindparam("period", type_=DATERANGE)


def _period(start: datetime.date, end_exclusive: datetime.date | None) -> Range[datetime.date]:
    return Range(start, end_exclusive, bounds="[)")


@dataclass(frozen=True, slots=True)
class Placed:
    """A record's raw period (upper bound exclusive), for the boundary arithmetic."""

    id: uuid.UUID
    start: datetime.date
    end: datetime.date | None  # exclusive; None = open-ended
    row_version: int
    fields: dict[str, Any]


async def _lock_employee(db: AsyncSession, employee_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                text(
                    "SELECT id, status, date_of_joining FROM core.employees "
                    "WHERE id = :e FOR UPDATE"
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


async def _placed(db: AsyncSession, employee_id: uuid.UUID) -> list[Placed]:
    rows = (
        (
            await db.execute(
                text(
                    f"SELECT {', '.join(FIELDS)}, id, lower(valid_during) AS start, "  # noqa: S608
                    "upper(valid_during) AS finish, row_version "
                    "FROM core.employee_job_records WHERE employee_id = :e ORDER BY valid_during"
                ),
                {"e": employee_id},
            )
        )
        .mappings()
        .all()
    )
    return [
        Placed(r["id"], r["start"], r["finish"], r["row_version"], {f: r[f] for f in FIELDS})
        for r in rows
    ]


async def list_records(db: AsyncSession, employee_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM core.employee_job_records "  # noqa: S608
                "WHERE employee_id = :e ORDER BY valid_during DESC"
            ),
            {"e": employee_id},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def get_record(db: AsyncSession, record_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                text(f"SELECT {_COLUMNS} FROM core.employee_job_records WHERE id = :id"),  # noqa: S608
                {"id": record_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFoundError("Job record not found.")
    return dict(row)


async def _validate(db: AsyncSession, employee_id: uuid.UUID, fields: dict[str, Any]) -> None:
    for column, (table, noun) in _REFERENCES.items():
        value = fields.get(column)
        if value is None:
            if column not in OPTIONAL:
                raise UnprocessableError(f"Choose a {noun}.", code="missing_field")
            continue
        archived: bool | None = (
            await db.execute(
                text(f"SELECT archived_at IS NOT NULL FROM {table} WHERE id = :id"),  # noqa: S608
                {"id": value},
            )
        ).scalar_one_or_none()
        if archived is None:
            raise UnprocessableError(f"That {noun} doesn't exist.", code="unknown_reference")
        if archived:
            raise UnprocessableError(f"That {noun} is archived.", code="archived_reference")
    manager = fields.get("manager_employee_id")
    if manager is not None:
        if manager == employee_id:
            raise UnprocessableError("Someone can't be their own manager.", code="reporting_cycle")
        status: str | None = (
            await db.execute(
                text("SELECT status FROM core.employees WHERE id = :id"), {"id": manager}
            )
        ).scalar_one_or_none()
        if status is None:
            raise UnprocessableError("That manager doesn't exist.", code="unknown_reference")
        if status == "exited":
            raise UnprocessableError("That manager has left.", code="manager_exited")
        await hierarchy.ensure_no_cycle(db, employee_id, manager)


async def _insert(
    db: AsyncSession,
    employee_id: uuid.UUID,
    period: Range[datetime.date],
    fields: dict[str, Any],
    reason: str,
    notes: str | None,
) -> uuid.UUID:
    statement = text(
        f"INSERT INTO core.employee_job_records (employee_id, valid_during, {', '.join(FIELDS)}, "  # noqa: S608
        "  change_reason, notes) "
        f"VALUES (:employee_id, :period, {', '.join(':' + f for f in FIELDS)}, :reason, :notes) "
        "RETURNING id"
    ).bindparams(_RANGE)
    try:
        async with db.begin_nested():
            record_id: uuid.UUID = (
                await db.execute(
                    statement,
                    {
                        "employee_id": employee_id,
                        "period": period,
                        "reason": reason,
                        "notes": notes,
                        **{f: fields.get(f) for f in FIELDS},
                    },
                )
            ).scalar_one()
    except IntegrityError as exc:
        if pg_error(exc).sqlstate == EXCLUSION_VIOLATION:
            raise ConflictError(
                "That overlaps another job record.", code="job_record_overlap"
            ) from exc
        raise
    return record_id


async def create_first(
    db: AsyncSession,
    employee_id: uuid.UUID,
    start: datetime.date,
    fields: dict[str, Any],
    *,
    reason: str = "hire",
) -> uuid.UUID:
    """The employee's first record, from their joining date. Open-ended."""
    await _validate(db, employee_id, fields)
    record_id = await _insert(db, employee_id, _period(start, None), fields, reason, None)
    manager = fields.get("manager_employee_id")
    if manager is not None and start <= await _today(db):
        await hierarchy.move_subtree(db, employee_id, manager)
        await hierarchy.sync_manager_roles(db)
    return record_id


async def _today(db: AsyncSession) -> datetime.date:
    today: datetime.date = (await db.execute(text("SELECT core.today()"))).scalar_one()
    return today


async def change(
    db: AsyncSession,
    employee_id: uuid.UUID,
    effective_from: datetime.date,
    changes: dict[str, Any],
    *,
    reason: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Open a new record from ``effective_from``, closing the one in force that day."""
    await _lock_employee(db, employee_id)
    records = await _placed(db, employee_id)
    if not records:
        raise UnprocessableError("This employee has no job record yet.", code="no_job_record")
    if effective_from < records[0].start:
        raise UnprocessableError(
            "A change can't start before the employee's first record.", code="before_hire"
        )
    base = next(
        (
            r
            for r in records
            if r.start <= effective_from and (r.end is None or effective_from < r.end)
        ),
        None,
    )
    if base is None:
        raise UnprocessableError(
            "This employee's last record has ended; there is nothing to change.",
            code="employee_exited",
        )
    if base.start == effective_from:
        raise ConflictError(
            "A record already starts on that date. Correct it instead.",
            code="record_starts_that_day",
        )
    fields = {**base.fields, **{k: v for k, v in changes.items() if k in FIELDS}}
    if fields == base.fields:
        raise UnprocessableError("Nothing would change.", code="no_change")
    await _validate(db, employee_id, fields)

    was_current_manager = await _current_manager(db, employee_id)
    shrink = text(
        "UPDATE core.employee_job_records SET valid_during = :period WHERE id = :id"
    ).bindparams(_RANGE)
    await db.execute(shrink, {"id": base.id, "period": _period(base.start, effective_from)})
    record_id = await _insert(
        db, employee_id, _period(effective_from, base.end), fields, reason, notes
    )
    await _after_change(db, employee_id, was_current_manager, reason_event="changed")
    await emit_event(
        db,
        aggregate_type="employee",
        aggregate_id=employee_id,
        event_type="core.job_record.changed",
        payload={"employee_id": str(employee_id), "effective_from": effective_from.isoformat()},
    )
    return await get_record(db, record_id)


async def _current_manager(db: AsyncSession, employee_id: uuid.UUID) -> uuid.UUID | None:
    manager: uuid.UUID | None = (
        await db.execute(
            text(
                "SELECT manager_employee_id FROM core.employee_job_records_current "
                "WHERE employee_id = :e"
            ),
            {"e": employee_id},
        )
    ).scalar_one_or_none()
    return manager


async def _after_change(
    db: AsyncSession, employee_id: uuid.UUID, manager_before: uuid.UUID | None, *, reason_event: str
) -> None:
    """If the manager in force today changed, re-hang the employee's subtree."""
    manager_after = await _current_manager(db, employee_id)
    if manager_after != manager_before:
        await hierarchy.move_subtree(db, employee_id, manager_after)
        await hierarchy.sync_manager_roles(db)


async def correct(
    db: AsyncSession,
    record_id: uuid.UUID,
    row_version: int,
    changes: dict[str, Any],
    *,
    start: datetime.date | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Fix a record in place. ``start`` moves the day the record begins (its neighbour follows)."""
    current = await get_record(db, record_id)
    employee_id: uuid.UUID = current["employee_id"]
    await _lock_employee(db, employee_id)
    if current["row_version"] != row_version:
        raise ConflictError(
            "Someone else changed this record. Reload and try again.", code="stale_row_version"
        )
    records = await _placed(db, employee_id)
    index = next(i for i, r in enumerate(records) if r.id == record_id)
    me = records[index]
    fields = {**me.fields, **{k: v for k, v in changes.items() if k in FIELDS}}
    await _validate(db, employee_id, fields)
    manager_before = await _current_manager(db, employee_id)

    if start is not None and start != me.start:
        previous = records[index - 1] if index > 0 else None
        if previous is None:
            raise UnprocessableError(
                "The first record starts on the joining date; change that on the employee.",
                code="first_record",
            )
        if start <= previous.start or (me.end is not None and start >= me.end):
            raise UnprocessableError(
                "The start must fall between the neighbouring records' starts.",
                code="invalid_start",
            )
        move = text(
            "UPDATE core.employee_job_records SET valid_during = :period WHERE id = :id"
        ).bindparams(_RANGE)
        # Shrink whichever side gives way first, so the two never overlap in between.
        if start > me.start:
            await db.execute(move, {"id": me.id, "period": _period(start, me.end)})
            await db.execute(move, {"id": previous.id, "period": _period(previous.start, start)})
        else:
            await db.execute(move, {"id": previous.id, "period": _period(previous.start, start)})
            await db.execute(move, {"id": me.id, "period": _period(start, me.end)})

    sets = ", ".join(f"{f} = :{f}" for f in FIELDS)
    await db.execute(
        text(
            f"UPDATE core.employee_job_records SET {sets}, "  # noqa: S608
            "notes = coalesce(:notes, notes) WHERE id = :id"
        ),
        {"id": record_id, "notes": notes, **{f: fields.get(f) for f in FIELDS}},
    )
    await _after_change(db, employee_id, manager_before, reason_event="corrected")
    await emit_event(
        db,
        aggregate_type="employee",
        aggregate_id=employee_id,
        event_type="core.job_record.corrected",
        payload={"employee_id": str(employee_id), "record_id": str(record_id)},
    )
    return await get_record(db, record_id)
