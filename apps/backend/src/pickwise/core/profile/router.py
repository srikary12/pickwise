# SPDX-License-Identifier: AGPL-3.0-only
"""An employee's profile tabs (/v1/employees/{id}/…).

Personal details, addresses, contacts and family need ``core.employee.personal.*``; education,
experience and documents need ``core.employees.*``. Each call checks the employee is within the
caller's data scope for that permission and answers 404 for anyone outside it.
"""

import json
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response, status

from pickwise.core.employees import service as employees
from pickwise.core.profile import service
from pickwise.core.profile.schemas import (
    AddressOut,
    AddressSet,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    DependentCreate,
    DependentOut,
    DependentUpdate,
    DocumentCreate,
    DocumentOut,
    EducationCreate,
    EducationOut,
    EducationUpdate,
    ExperienceCreate,
    ExperienceOut,
    ExperienceUpdate,
    NominationOut,
    NominationsSet,
    PersonalOut,
    PersonalUpdate,
    Scheme,
)
from pickwise.platform import audit
from pickwise.platform.auth.dependencies import DB, Authorized, require
from pickwise.platform.idempotency import idempotent

router = APIRouter(prefix="/v1/employees/{employee_id}", tags=["employee profile"])

PersonalRead = Annotated[Authorized, Depends(require("core.employee.personal.read"))]
PersonalWrite = Annotated[Authorized, Depends(require("core.employee.personal.update"))]
Read = Annotated[Authorized, Depends(require("core.employees.read"))]
Write = Annotated[Authorized, Depends(require("core.employees.update"))]
Row = dict[str, Any]
Rows = list[dict[str, Any]]


async def _scope(db: DB, auth: Authorized, employee_id: uuid.UUID) -> None:
    await employees.get(db, auth.principal, auth.scopes, employee_id)


def _json(row: Row) -> Row:
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
    employee_id: uuid.UUID,
    make: Any,
) -> Any:
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    await _scope(db, auth, employee_id)
    row: Row = await make()
    await audit.record(db, action, "core.employees", employee_id)
    body = _json(row)
    await idem.finish(status.HTTP_201_CREATED, body)
    response.status_code = status.HTTP_201_CREATED
    return body


# --- personal ---------------------------------------------------------------------------------


@router.get("/personal", response_model=PersonalOut)
async def get_personal(employee_id: uuid.UUID, db: DB, auth: PersonalRead) -> Row:
    await _scope(db, auth, employee_id)
    return await service.get_personal(db, employee_id)


@router.put("/personal", response_model=PersonalOut)
async def save_personal(
    employee_id: uuid.UUID, body: PersonalUpdate, db: DB, auth: PersonalWrite
) -> Row:
    await _scope(db, auth, employee_id)
    row = await service.save_personal(
        db, employee_id, body.model_dump(exclude={"row_version"}), body.row_version
    )
    await audit.record(db, "employee.personal_changed", "core.employees", employee_id)
    return row


# --- addresses --------------------------------------------------------------------------------


@router.get("/addresses", response_model=list[AddressOut])
async def list_addresses(employee_id: uuid.UUID, db: DB, auth: PersonalRead) -> Rows:
    """Current and past addresses, newest first within each type."""
    await _scope(db, auth, employee_id)
    return await service.list_addresses(db, employee_id)


@router.put("/addresses/{address_type}", response_model=AddressOut)
async def set_address(
    employee_id: uuid.UUID,
    address_type: Literal["current", "permanent"],
    body: AddressSet,
    db: DB,
    auth: PersonalWrite,
) -> Row:
    """Set the current or permanent address from a date (today by default); the previous one
    ends the day before and stays in the history."""
    await _scope(db, auth, employee_id)
    row = await service.set_address(
        db, employee_id, address_type, body.model_dump(exclude={"valid_from"}), body.valid_from
    )
    await audit.record(db, "employee.address_changed", "core.employees", employee_id)
    return row


# --- emergency contacts -----------------------------------------------------------------------


@router.get("/emergency-contacts", response_model=list[ContactOut])
async def list_contacts(employee_id: uuid.UUID, db: DB, auth: PersonalRead) -> Rows:
    await _scope(db, auth, employee_id)
    return await service.list_for(db, service.EMERGENCY, employee_id)


@router.post("/emergency-contacts", response_model=ContactOut, status_code=201)
async def add_contact(
    employee_id: uuid.UUID,
    body: ContactCreate,
    request: Request,
    response: Response,
    db: DB,
    auth: PersonalWrite,
) -> Any:
    return await _create(
        request,
        response,
        db,
        auth,
        "employee.contact_added",
        employee_id,
        lambda: service.add_contact(db, employee_id, body.model_dump()),
    )


@router.put("/emergency-contacts/{contact_id}", response_model=ContactOut)
async def edit_contact(
    employee_id: uuid.UUID, contact_id: uuid.UUID, body: ContactUpdate, db: DB, auth: PersonalWrite
) -> Row:
    await _scope(db, auth, employee_id)
    row = await service.edit_contact(
        db, employee_id, contact_id, body.model_dump(exclude={"row_version"}), body.row_version
    )
    await audit.record(db, "employee.contact_changed", "core.employees", employee_id)
    return row


@router.delete("/emergency-contacts/{contact_id}", status_code=204)
async def delete_contact(
    employee_id: uuid.UUID, contact_id: uuid.UUID, db: DB, auth: PersonalWrite
) -> None:
    await _scope(db, auth, employee_id)
    await service.remove(db, service.EMERGENCY, employee_id, contact_id)
    await audit.record(db, "employee.contact_removed", "core.employees", employee_id)


# --- dependents and nominations ---------------------------------------------------------------


@router.get("/dependents", response_model=list[DependentOut])
async def list_dependents(employee_id: uuid.UUID, db: DB, auth: PersonalRead) -> Rows:
    await _scope(db, auth, employee_id)
    return await service.list_for(db, service.DEPENDENT, employee_id)


@router.post("/dependents", response_model=DependentOut, status_code=201)
async def add_dependent(
    employee_id: uuid.UUID,
    body: DependentCreate,
    request: Request,
    response: Response,
    db: DB,
    auth: PersonalWrite,
) -> Any:
    return await _create(
        request,
        response,
        db,
        auth,
        "employee.dependent_added",
        employee_id,
        lambda: service.add(db, service.DEPENDENT, employee_id, body.model_dump()),
    )


@router.put("/dependents/{dependent_id}", response_model=DependentOut)
async def edit_dependent(
    employee_id: uuid.UUID,
    dependent_id: uuid.UUID,
    body: DependentUpdate,
    db: DB,
    auth: PersonalWrite,
) -> Row:
    await _scope(db, auth, employee_id)
    row = await service.edit(
        db,
        service.DEPENDENT,
        employee_id,
        dependent_id,
        body.model_dump(exclude={"row_version"}),
        body.row_version,
    )
    await audit.record(db, "employee.dependent_changed", "core.employees", employee_id)
    return row


@router.delete("/dependents/{dependent_id}", status_code=204)
async def delete_dependent(
    employee_id: uuid.UUID, dependent_id: uuid.UUID, db: DB, auth: PersonalWrite
) -> None:
    """A dependent who is nominated can't be removed until the nomination is changed."""
    await _scope(db, auth, employee_id)
    await service.remove(db, service.DEPENDENT, employee_id, dependent_id)
    await audit.record(db, "employee.dependent_removed", "core.employees", employee_id)


@router.get("/nominations", response_model=list[NominationOut])
async def list_nominations(employee_id: uuid.UUID, db: DB, auth: PersonalRead) -> Rows:
    await _scope(db, auth, employee_id)
    return await service.list_nominations(db, employee_id)


@router.put("/nominations/{scheme}", response_model=list[NominationOut])
async def set_nominations(
    employee_id: uuid.UUID, scheme: Scheme, body: NominationsSet, db: DB, auth: PersonalWrite
) -> Rows:
    """Replace one scheme's nominees. Shares must total 100."""
    await _scope(db, auth, employee_id)
    rows = await service.set_nominations(
        db, employee_id, scheme, [s.model_dump() for s in body.shares]
    )
    await audit.record(
        db, "employee.nominations_changed", "core.employees", employee_id, {"scheme": scheme}
    )
    return rows


# --- education and experience -----------------------------------------------------------------


@router.get("/education", response_model=list[EducationOut])
async def list_education(employee_id: uuid.UUID, db: DB, auth: Read) -> Rows:
    await _scope(db, auth, employee_id)
    return await service.list_for(db, service.EDUCATION, employee_id)


@router.post("/education", response_model=EducationOut, status_code=201)
async def add_education(
    employee_id: uuid.UUID,
    body: EducationCreate,
    request: Request,
    response: Response,
    db: DB,
    auth: Write,
) -> Any:
    return await _create(
        request,
        response,
        db,
        auth,
        "employee.education_added",
        employee_id,
        lambda: service.add(db, service.EDUCATION, employee_id, body.model_dump()),
    )


@router.put("/education/{entry_id}", response_model=EducationOut)
async def edit_education(
    employee_id: uuid.UUID, entry_id: uuid.UUID, body: EducationUpdate, db: DB, auth: Write
) -> Row:
    await _scope(db, auth, employee_id)
    return await service.edit(
        db,
        service.EDUCATION,
        employee_id,
        entry_id,
        body.model_dump(exclude={"row_version"}),
        body.row_version,
    )


@router.delete("/education/{entry_id}", status_code=204)
async def delete_education(
    employee_id: uuid.UUID, entry_id: uuid.UUID, db: DB, auth: Write
) -> None:
    await _scope(db, auth, employee_id)
    await service.remove(db, service.EDUCATION, employee_id, entry_id)


@router.get("/experience", response_model=list[ExperienceOut])
async def list_experience(employee_id: uuid.UUID, db: DB, auth: Read) -> Rows:
    await _scope(db, auth, employee_id)
    return await service.list_for(db, service.EXPERIENCE, employee_id)


@router.post("/experience", response_model=ExperienceOut, status_code=201)
async def add_experience(
    employee_id: uuid.UUID,
    body: ExperienceCreate,
    request: Request,
    response: Response,
    db: DB,
    auth: Write,
) -> Any:
    return await _create(
        request,
        response,
        db,
        auth,
        "employee.experience_added",
        employee_id,
        lambda: service.add(db, service.EXPERIENCE, employee_id, body.model_dump()),
    )


@router.put("/experience/{entry_id}", response_model=ExperienceOut)
async def edit_experience(
    employee_id: uuid.UUID, entry_id: uuid.UUID, body: ExperienceUpdate, db: DB, auth: Write
) -> Row:
    await _scope(db, auth, employee_id)
    return await service.edit(
        db,
        service.EXPERIENCE,
        employee_id,
        entry_id,
        body.model_dump(exclude={"row_version"}),
        body.row_version,
    )


@router.delete("/experience/{entry_id}", status_code=204)
async def delete_experience(
    employee_id: uuid.UUID, entry_id: uuid.UUID, db: DB, auth: Write
) -> None:
    await _scope(db, auth, employee_id)
    await service.remove(db, service.EXPERIENCE, employee_id, entry_id)


# --- documents --------------------------------------------------------------------------------


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(employee_id: uuid.UUID, db: DB, auth: Read) -> Rows:
    """Files kept on the employee's record. Download one with `GET /v1/files/{file_id}/download`."""
    await _scope(db, auth, employee_id)
    return await service.list_for(db, service.DOCUMENT, employee_id)


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def add_document(
    employee_id: uuid.UUID,
    body: DocumentCreate,
    request: Request,
    response: Response,
    db: DB,
    auth: Write,
) -> Any:
    async def make() -> Row:
        await service.check_document_file(db, auth, employee_id, body.file_id)
        return await service.add(db, service.DOCUMENT, employee_id, body.model_dump())

    return await _create(request, response, db, auth, "employee.document_added", employee_id, make)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    employee_id: uuid.UUID, document_id: uuid.UUID, db: DB, auth: Write
) -> None:
    await _scope(db, auth, employee_id)
    await service.remove(db, service.DOCUMENT, employee_id, document_id)
    await audit.record(db, "employee.document_removed", "core.employees", employee_id)
