# SPDX-License-Identifier: AGPL-3.0-only
"""Employees (/v1/employees), their job history, the directory and the org chart.

Every employee-scoped call first checks the employee is within the caller's data scope for
that permission, and answers 404 (not 403) for anyone outside it.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import text

from pickwise.core.employees import service
from pickwise.core.employees.schemas import (
    DirectoryEntry,
    DirectoryPage,
    EmployeeCreate,
    EmployeeOut,
    EmployeePage,
    EmployeeStatus,
    EmployeeUpdate,
    LinkUser,
    OrgChart,
    StatusChange,
)
from pickwise.core.job_records import service as jobs
from pickwise.core.job_records.schemas import JobChange, JobCorrection, JobRecordOut
from pickwise.platform import audit
from pickwise.platform.auth.dependencies import DB, Authorized, require
from pickwise.platform.idempotency import idempotent
from pickwise.platform.scopes import ScopeType
from pickwise.shared.errors import ForbiddenError

router = APIRouter(tags=["employees"])

Read = Annotated[Authorized, Depends(require("core.employees.read"))]
Create = Annotated[Authorized, Depends(require("core.employees.create"))]
Update = Annotated[Authorized, Depends(require("core.employees.update"))]
JobsRead = Annotated[Authorized, Depends(require("core.jobrecords.read"))]
JobsChange = Annotated[Authorized, Depends(require("core.jobrecords.change"))]
Directory = Annotated[Authorized, Depends(require("core.directory.read"))]
SENSITIVE_FIELDS = "platform.custom_fields.read_sensitive"
Limit = Annotated[int, Query(ge=1, le=200)]


async def _employee(db: DB, auth: Authorized, employee_id: uuid.UUID) -> dict[str, Any]:
    return service.present(await service.get(db, auth.principal, auth.scopes, employee_id))


def _filters(
    q: str | None,
    status_: EmployeeStatus | None,
    department_id: uuid.UUID | None,
    location_id: uuid.UUID | None,
    legal_entity_id: uuid.UUID | None,
    manager_id: uuid.UUID | None,
) -> service.Filters:
    return service.Filters(
        q=q,
        status=status_,
        department_id=department_id,
        location_id=location_id,
        legal_entity_id=legal_entity_id,
        manager_id=manager_id,
    )


@router.get("/v1/employees", response_model=EmployeePage)
async def list_employees(
    db: DB,
    auth: Read,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status_: Annotated[EmployeeStatus | None, Query(alias="status")] = None,
    department_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    legal_entity_id: uuid.UUID | None = None,
    manager_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> EmployeePage:
    """Employees within your data scope, by name. Page with `next_cursor`."""
    f = _filters(q, status_, department_id, location_id, legal_entity_id, manager_id)
    rows, next_cursor = await service.page(
        db, auth.principal, auth.scopes, f, cursor=cursor, limit=limit
    )
    return EmployeePage(
        items=[EmployeeOut(**service.present(r)) for r in rows], next_cursor=next_cursor
    )


@router.post("/v1/employees", response_model=EmployeeOut, status_code=201)
async def create_employee(
    body: EmployeeCreate, request: Request, response: Response, db: DB, auth: Create
) -> Any:
    """Add an employee as a draft with their first job record. The code comes from the tenant's
    employee-code sequence."""
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    values = body.model_dump(exclude={"job"})
    job = body.job.model_dump()
    employee_id = await service.create(
        db, values, job, can_edit_sensitive=SENSITIVE_FIELDS in auth.grants
    )
    await audit.record(db, "employee.created", "core.employees", employee_id)
    out = EmployeeOut(
        **service.present(await service.get(db, auth.principal, auth.scopes, employee_id))
    )
    payload = out.model_dump(mode="json")
    await idem.finish(status.HTTP_201_CREATED, payload)
    response.status_code = status.HTTP_201_CREATED
    return payload


@router.get("/v1/employees/{employee_id}", response_model=EmployeeOut)
async def get_employee(employee_id: uuid.UUID, db: DB, auth: Read) -> dict[str, Any]:
    return await _employee(db, auth, employee_id)


@router.put("/v1/employees/{employee_id}", response_model=EmployeeOut)
async def update_employee(
    employee_id: uuid.UUID, body: EmployeeUpdate, db: DB, auth: Update
) -> dict[str, Any]:
    await _employee(db, auth, employee_id)
    await service.update(
        db,
        employee_id,
        body.model_dump(exclude={"row_version"}),
        body.row_version,
        can_edit_sensitive=SENSITIVE_FIELDS in auth.grants,
    )
    return await _employee(db, auth, employee_id)


@router.post("/v1/employees/{employee_id}/status", response_model=EmployeeOut)
async def change_status(
    employee_id: uuid.UUID, body: StatusChange, db: DB, auth: Update
) -> dict[str, Any]:
    """Draft → pre-boarding → active. Leaving and leave of absence come with later features."""
    await _employee(db, auth, employee_id)
    await service.set_status(db, employee_id, body.to, body.row_version)
    await audit.record(
        db, "employee.status_changed", "core.employees", employee_id, {"to": body.to}
    )
    return await _employee(db, auth, employee_id)


@router.put("/v1/employees/{employee_id}/user", response_model=EmployeeOut)
async def link_user(employee_id: uuid.UUID, body: LinkUser, db: DB, auth: Update) -> dict[str, Any]:
    """Connect the employee to a person who can sign in (their membership), so the person sees
    their own record. Send null to disconnect."""
    await _employee(db, auth, employee_id)
    await service.link_user(db, employee_id, body.membership_id, body.row_version)
    await audit.record(
        db,
        "employee.user_linked" if body.membership_id else "employee.user_unlinked",
        "core.employees",
        employee_id,
    )
    return await _employee(db, auth, employee_id)


# --- job history -----------------------------------------------------------------------------


@router.get("/v1/employees/{employee_id}/job-records", response_model=list[JobRecordOut])
async def list_job_records(employee_id: uuid.UUID, db: DB, auth: JobsRead) -> list[dict[str, Any]]:
    """Newest first. The `is_current` one is in force today; later ones are scheduled."""
    await service.get(db, auth.principal, auth.scopes, employee_id)
    return await jobs.list_records(db, employee_id)


@router.post(
    "/v1/employees/{employee_id}/job-records", response_model=JobRecordOut, status_code=201
)
async def change_job(
    employee_id: uuid.UUID,
    body: JobChange,
    request: Request,
    response: Response,
    db: DB,
    auth: JobsChange,
) -> Any:
    """Promote, transfer or change the manager from a date. The record in force that day is
    closed the day before; nothing is overwritten. A future date schedules the change."""
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    await service.get(db, auth.principal, auth.scopes, employee_id)
    changes = {k: v for k, v in body.model_dump().items() if k in body.model_fields_set}
    record = await jobs.change(
        db,
        employee_id,
        body.effective_from,
        changes,
        reason=body.reason,
        notes=body.notes,
    )
    await audit.record(
        db,
        "employee.job_changed",
        "core.employees",
        employee_id,
        {"effective_from": body.effective_from.isoformat(), "reason": body.reason},
    )
    payload = JobRecordOut(**record).model_dump(mode="json")
    await idem.finish(status.HTTP_201_CREATED, payload)
    response.status_code = status.HTTP_201_CREATED
    return payload


@router.put("/v1/job-records/{record_id}", response_model=JobRecordOut)
async def correct_job_record(
    record_id: uuid.UUID, body: JobCorrection, db: DB, auth: JobsChange
) -> dict[str, Any]:
    """Fix a mistake in a record in place (the audit trail keeps what it said)."""
    current = await jobs.get_record(db, record_id)
    await service.get(db, auth.principal, auth.scopes, current["employee_id"])
    changes = {
        k: v
        for k, v in body.model_dump().items()
        if k in body.model_fields_set and k not in ("row_version", "start", "notes")
    }
    record = await jobs.correct(
        db, record_id, body.row_version, changes, start=body.start, notes=body.notes
    )
    await audit.record(
        db,
        "employee.job_corrected",
        "core.employees",
        current["employee_id"],
        {"record": str(record_id)},
    )
    return record


# --- directory and org chart -------------------------------------------------------------------


@router.get("/v1/directory", response_model=DirectoryPage)
async def directory(
    db: DB,
    auth: Directory,
    q: Annotated[str | None, Query(max_length=100)] = None,
    department_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> DirectoryPage:
    """Colleagues who are working here, with work details only (never personal data)."""
    f = service.Filters(q=q, department_id=department_id, location_id=location_id)
    rows, next_cursor = await service.page(
        db, auth.principal, auth.scopes, f, cursor=cursor, limit=limit, only_live=True
    )
    items = []
    for row in rows:
        r = service.present(row)
        job = r["job"] or {}
        items.append(
            DirectoryEntry(
                id=r["id"],
                employee_code=r["employee_code"],
                display_name=r["display_name"],
                work_email=r["work_email"],
                work_phone=r["work_phone"],
                photo_file_id=r["photo_file_id"],
                designation_name=job.get("designation_name"),
                department_id=job.get("department_id"),
                department_name=job.get("department_name"),
                location_name=job.get("location_name"),
                manager_employee_id=job.get("manager_employee_id"),
                manager_name=job.get("manager_name"),
            )
        )
    return DirectoryPage(items=items, next_cursor=next_cursor)


@router.get("/v1/directory/org-chart", response_model=OrgChart)
async def org_chart(db: DB, auth: Directory) -> OrgChart:
    """Everyone working here with their manager and number of direct reports, for drawing the
    reporting tree. Work details only. Needs the directory at tenant scope: the chart isn't
    filtered by narrower scopes."""
    if not any(scope.type is ScopeType.TENANT for scope in auth.scopes):
        raise ForbiddenError("The org chart needs company-wide directory access.")
    rows = (
        await db.execute(
            text(
                "SELECT e.id, e.display_name, g.name AS designation_name, "
                "  d.name AS department_name, "
                "  j.manager_employee_id, "
                "  (SELECT count(*) FROM core.employee_job_records_current r "
                "   JOIN core.employees re ON re.tenant_id = r.tenant_id AND re.id = r.employee_id "
                "   WHERE r.manager_employee_id = e.id "
                "     AND re.status IN ('active', 'notice_period', 'leave_of_absence')) AS reports "
                "FROM core.employees e "
                "LEFT JOIN core.employee_job_records_current j ON j.employee_id = e.id "
                "LEFT JOIN core.designations g ON g.id = j.designation_id "
                "LEFT JOIN core.departments d ON d.id = j.department_id "
                "WHERE e.status IN ('active', 'notice_period', 'leave_of_absence') "
                "ORDER BY lower(e.display_name), e.id LIMIT 5000"
            )
        )
    ).mappings()
    return OrgChart(
        nodes=[
            {
                "id": r["id"],
                "display_name": r["display_name"],
                "designation_name": r["designation_name"],
                "department_name": r["department_name"],
                "manager_employee_id": r["manager_employee_id"],
                "report_count": r["reports"],
            }
            for r in rows
        ]
    )
