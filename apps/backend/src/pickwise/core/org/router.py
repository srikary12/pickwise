# SPDX-License-Identifier: AGPL-3.0-only
"""Organisation structure (/v1/org): legal entities, locations, cost centres, designations,
grades and the department tree. Reading needs ``core.org.read`` (every role); changes need
``core.org.manage``. Archiving replaces deleting so history that points at a row stays valid."""

import json
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel

from pickwise.core import crud
from pickwise.core.org import service
from pickwise.core.org.schemas import (
    CostCenterCreate,
    CostCenterOut,
    CostCenterUpdate,
    DepartmentCreate,
    DepartmentMove,
    DepartmentOut,
    DepartmentUpdate,
    DesignationCreate,
    DesignationOut,
    DesignationUpdate,
    GradeCreate,
    GradeOut,
    GradeUpdate,
    LegalEntityCreate,
    LegalEntityOut,
    LegalEntityUpdate,
    LocationCreate,
    LocationOut,
    LocationUpdate,
    RegistrationCreate,
    RegistrationOut,
    RegistrationUpdate,
)
from pickwise.platform import audit
from pickwise.platform.auth.dependencies import DB, Authorized, require
from pickwise.platform.idempotency import idempotent

router = APIRouter(prefix="/v1/org", tags=["org"])

Read = Annotated[Authorized, Depends(require("core.org.read"))]
Manage = Annotated[Authorized, Depends(require("core.org.manage"))]
IncludeArchived = Annotated[bool, Query(description="Also list archived rows.")]
Rows = list[dict[str, Any]]
Row = dict[str, Any]


def _dump(body: BaseModel) -> Row:
    return body.model_dump(exclude={"row_version"})


def _json(row: Row) -> Row:
    """The row as JSON-safe values, to store as the response of an idempotent request."""

    def default(value: Any) -> Any:
        return str(value) if isinstance(value, uuid.UUID | Decimal) else value.isoformat()

    parsed: Row = json.loads(json.dumps(row, default=default))
    return parsed


async def _create(
    request: Request,
    response: Response,
    db: DB,
    auth: Authorized,
    action: str,
    table: str,
    make: Callable[[], Awaitable[Row]],
) -> Any:
    """A POST that creates: a repeated Idempotency-Key replays the stored answer."""
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    row = await make()
    await audit.record(db, action, table, row["id"])
    body = _json(row)
    await idem.finish(status.HTTP_201_CREATED, body)
    response.status_code = status.HTTP_201_CREATED
    return body


# --- legal entities --------------------------------------------------------------------------


@router.get("/legal-entities", response_model=list[LegalEntityOut])
async def list_legal_entities(
    db: DB, auth: Read, include_archived: IncludeArchived = False
) -> Rows:
    return await crud.list_rows(db, service.LEGAL_ENTITY, include_archived=include_archived)


@router.post("/legal-entities", response_model=LegalEntityOut, status_code=201)
async def create_legal_entity(
    body: LegalEntityCreate, request: Request, response: Response, db: DB, auth: Manage
) -> Any:
    return await _create(
        request,
        response,
        db,
        auth,
        "org.legal_entity.created",
        "core.legal_entities",
        lambda: crud.insert_row(db, service.LEGAL_ENTITY, _dump(body)),
    )


@router.put("/legal-entities/{entity_id}", response_model=LegalEntityOut)
async def update_legal_entity(
    entity_id: uuid.UUID, body: LegalEntityUpdate, db: DB, auth: Manage
) -> Row:
    row = await crud.update_row(db, service.LEGAL_ENTITY, entity_id, _dump(body), body.row_version)
    await audit.record(db, "org.legal_entity.updated", "core.legal_entities", entity_id)
    return row


@router.post("/legal-entities/{entity_id}/archive", response_model=LegalEntityOut)
async def archive_legal_entity(entity_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.LEGAL_ENTITY, entity_id, True)
    await audit.record(db, "org.legal_entity.archived", "core.legal_entities", entity_id)
    return row


@router.post("/legal-entities/{entity_id}/unarchive", response_model=LegalEntityOut)
async def unarchive_legal_entity(entity_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.LEGAL_ENTITY, entity_id, False)
    await audit.record(db, "org.legal_entity.unarchived", "core.legal_entities", entity_id)
    return row


@router.get("/legal-entities/{entity_id}/registrations", response_model=list[RegistrationOut])
async def list_registrations(entity_id: uuid.UUID, db: DB, auth: Read) -> Rows:
    return await service.list_registrations(db, entity_id)


@router.post(
    "/legal-entities/{entity_id}/registrations", response_model=RegistrationOut, status_code=201
)
async def create_registration(
    entity_id: uuid.UUID,
    body: RegistrationCreate,
    request: Request,
    response: Response,
    db: DB,
    auth: Manage,
) -> Any:
    return await _create(
        request,
        response,
        db,
        auth,
        "org.registration.created",
        "core.legal_entity_registrations",
        lambda: service.create_registration(db, entity_id, _dump(body)),
    )


@router.put(
    "/legal-entities/{entity_id}/registrations/{registration_id}",
    response_model=RegistrationOut,
)
async def update_registration(
    entity_id: uuid.UUID,
    registration_id: uuid.UUID,
    body: RegistrationUpdate,
    db: DB,
    auth: Manage,
) -> Row:
    row = await service.update_registration(
        db, entity_id, registration_id, _dump(body), body.row_version
    )
    await audit.record(
        db, "org.registration.updated", "core.legal_entity_registrations", registration_id
    )
    return row


@router.delete(
    "/legal-entities/{entity_id}/registrations/{registration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_registration(
    entity_id: uuid.UUID, registration_id: uuid.UUID, db: DB, auth: Manage
) -> None:
    await service.delete_registration(db, entity_id, registration_id)
    await audit.record(
        db, "org.registration.deleted", "core.legal_entity_registrations", registration_id
    )


# --- locations -------------------------------------------------------------------------------


@router.get("/locations", response_model=list[LocationOut])
async def list_locations(db: DB, auth: Read, include_archived: IncludeArchived = False) -> Rows:
    return await crud.list_rows(db, service.LOCATION, include_archived=include_archived)


@router.post("/locations", response_model=LocationOut, status_code=201)
async def create_location(
    body: LocationCreate, request: Request, response: Response, db: DB, auth: Manage
) -> Any:
    service.check_timezone(body.timezone)
    return await _create(
        request,
        response,
        db,
        auth,
        "org.location.created",
        "core.locations",
        lambda: crud.insert_row(db, service.LOCATION, _dump(body)),
    )


@router.put("/locations/{location_id}", response_model=LocationOut)
async def update_location(
    location_id: uuid.UUID, body: LocationUpdate, db: DB, auth: Manage
) -> Row:
    service.check_timezone(body.timezone)
    row = await crud.update_row(db, service.LOCATION, location_id, _dump(body), body.row_version)
    await audit.record(db, "org.location.updated", "core.locations", location_id)
    return row


@router.post("/locations/{location_id}/archive", response_model=LocationOut)
async def archive_location(location_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.LOCATION, location_id, True)
    await audit.record(db, "org.location.archived", "core.locations", location_id)
    return row


@router.post("/locations/{location_id}/unarchive", response_model=LocationOut)
async def unarchive_location(location_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.LOCATION, location_id, False)
    await audit.record(db, "org.location.unarchived", "core.locations", location_id)
    return row


# --- cost centres ----------------------------------------------------------------------------


@router.get("/cost-centers", response_model=list[CostCenterOut])
async def list_cost_centers(db: DB, auth: Read, include_archived: IncludeArchived = False) -> Rows:
    return await crud.list_rows(db, service.COST_CENTER, include_archived=include_archived)


@router.post("/cost-centers", response_model=CostCenterOut, status_code=201)
async def create_cost_center(
    body: CostCenterCreate, request: Request, response: Response, db: DB, auth: Manage
) -> Any:
    return await _create(
        request,
        response,
        db,
        auth,
        "org.cost_center.created",
        "core.cost_centers",
        lambda: crud.insert_row(db, service.COST_CENTER, _dump(body)),
    )


@router.put("/cost-centers/{cost_center_id}", response_model=CostCenterOut)
async def update_cost_center(
    cost_center_id: uuid.UUID, body: CostCenterUpdate, db: DB, auth: Manage
) -> Row:
    row = await crud.update_row(
        db, service.COST_CENTER, cost_center_id, _dump(body), body.row_version
    )
    await audit.record(db, "org.cost_center.updated", "core.cost_centers", cost_center_id)
    return row


@router.post("/cost-centers/{cost_center_id}/archive", response_model=CostCenterOut)
async def archive_cost_center(cost_center_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.COST_CENTER, cost_center_id, True)
    await audit.record(db, "org.cost_center.archived", "core.cost_centers", cost_center_id)
    return row


@router.post("/cost-centers/{cost_center_id}/unarchive", response_model=CostCenterOut)
async def unarchive_cost_center(cost_center_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.COST_CENTER, cost_center_id, False)
    await audit.record(db, "org.cost_center.unarchived", "core.cost_centers", cost_center_id)
    return row


# --- designations ----------------------------------------------------------------------------


@router.get("/designations", response_model=list[DesignationOut])
async def list_designations(db: DB, auth: Read, include_archived: IncludeArchived = False) -> Rows:
    return await crud.list_rows(db, service.DESIGNATION, include_archived=include_archived)


@router.post("/designations", response_model=DesignationOut, status_code=201)
async def create_designation(
    body: DesignationCreate, request: Request, response: Response, db: DB, auth: Manage
) -> Any:
    return await _create(
        request,
        response,
        db,
        auth,
        "org.designation.created",
        "core.designations",
        lambda: crud.insert_row(db, service.DESIGNATION, _dump(body)),
    )


@router.put("/designations/{designation_id}", response_model=DesignationOut)
async def update_designation(
    designation_id: uuid.UUID, body: DesignationUpdate, db: DB, auth: Manage
) -> Row:
    row = await crud.update_row(
        db, service.DESIGNATION, designation_id, _dump(body), body.row_version
    )
    await audit.record(db, "org.designation.updated", "core.designations", designation_id)
    return row


@router.post("/designations/{designation_id}/archive", response_model=DesignationOut)
async def archive_designation(designation_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.DESIGNATION, designation_id, True)
    await audit.record(db, "org.designation.archived", "core.designations", designation_id)
    return row


@router.post("/designations/{designation_id}/unarchive", response_model=DesignationOut)
async def unarchive_designation(designation_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.DESIGNATION, designation_id, False)
    await audit.record(db, "org.designation.unarchived", "core.designations", designation_id)
    return row


# --- grades ----------------------------------------------------------------------------------


@router.get("/grades", response_model=list[GradeOut])
async def list_grades(db: DB, auth: Read, include_archived: IncludeArchived = False) -> Rows:
    return await crud.list_rows(db, service.GRADE, include_archived=include_archived)


@router.post("/grades", response_model=GradeOut, status_code=201)
async def create_grade(
    body: GradeCreate, request: Request, response: Response, db: DB, auth: Manage
) -> Any:
    return await _create(
        request,
        response,
        db,
        auth,
        "org.grade.created",
        "core.grades",
        lambda: crud.insert_row(db, service.GRADE, _dump(body)),
    )


@router.put("/grades/{grade_id}", response_model=GradeOut)
async def update_grade(grade_id: uuid.UUID, body: GradeUpdate, db: DB, auth: Manage) -> Row:
    row = await crud.update_row(db, service.GRADE, grade_id, _dump(body), body.row_version)
    await audit.record(db, "org.grade.updated", "core.grades", grade_id)
    return row


@router.post("/grades/{grade_id}/archive", response_model=GradeOut)
async def archive_grade(grade_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.GRADE, grade_id, True)
    await audit.record(db, "org.grade.archived", "core.grades", grade_id)
    return row


@router.post("/grades/{grade_id}/unarchive", response_model=GradeOut)
async def unarchive_grade(grade_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    row = await crud.set_archived(db, service.GRADE, grade_id, False)
    await audit.record(db, "org.grade.unarchived", "core.grades", grade_id)
    return row


# --- departments -----------------------------------------------------------------------------


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(db: DB, auth: Read, include_archived: IncludeArchived = False) -> Rows:
    """Every department in tree order (parents before their children); `depth` and `parent_id`
    are enough to draw the tree."""
    return await crud.list_rows(db, service.DEPARTMENT, include_archived=include_archived)


@router.post("/departments", response_model=DepartmentOut, status_code=201)
async def create_department(
    body: DepartmentCreate, request: Request, response: Response, db: DB, auth: Manage
) -> Any:
    async def make() -> Row:
        row = await crud.insert_row(db, service.DEPARTMENT, _dump(body))
        return await crud.get_row(db, service.DEPARTMENT, row["id"])

    return await _create(
        request, response, db, auth, "org.department.created", "core.departments", make
    )


@router.put("/departments/{department_id}", response_model=DepartmentOut)
async def update_department(
    department_id: uuid.UUID, body: DepartmentUpdate, db: DB, auth: Manage
) -> Row:
    await crud.update_row(db, service.DEPARTMENT, department_id, _dump(body), body.row_version)
    await audit.record(db, "org.department.updated", "core.departments", department_id)
    return await crud.get_row(db, service.DEPARTMENT, department_id)


@router.post("/departments/{department_id}/move", response_model=DepartmentOut)
async def move_department(
    department_id: uuid.UUID, body: DepartmentMove, db: DB, auth: Manage
) -> Row:
    """Re-parent a department with its whole subtree. Moving it under itself or one of its own
    sub-departments is refused (422 `department_cycle`)."""
    await service.move_department(db, department_id, body.parent_id, body.row_version)
    await audit.record(
        db,
        "org.department.moved",
        "core.departments",
        department_id,
        {"parent_id": str(body.parent_id) if body.parent_id else None},
    )
    return await crud.get_row(db, service.DEPARTMENT, department_id)


@router.post("/departments/{department_id}/archive", response_model=DepartmentOut)
async def archive_department(department_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    await service.archive_department(db, department_id)
    await audit.record(db, "org.department.archived", "core.departments", department_id)
    return await crud.get_row(db, service.DEPARTMENT, department_id)


@router.post("/departments/{department_id}/unarchive", response_model=DepartmentOut)
async def unarchive_department(department_id: uuid.UUID, db: DB, auth: Manage) -> Row:
    await service.unarchive_department(db, department_id)
    await audit.record(db, "org.department.unarchived", "core.departments", department_id)
    return await crud.get_row(db, service.DEPARTMENT, department_id)
