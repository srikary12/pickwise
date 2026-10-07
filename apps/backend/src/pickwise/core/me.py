# SPDX-License-Identifier: AGPL-3.0-only
"""Self-service (/v1/me/employee): a person's own record, and the parts they may change.

Needs only ``core.me.read`` / ``core.me.update``, which every role holds; the employee is always
the caller's own (found through their membership), never taken from the URL. Identity and bank
details are shown masked. They can change their personal contact details, address and
emergency contacts, and nothing else.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends

from pickwise.core.employees import service as employees
from pickwise.core.employees.schemas import EmployeeOut
from pickwise.core.identity import service as identity
from pickwise.core.identity.schemas import BankOut, IdentityOut
from pickwise.core.job_records import service as jobs
from pickwise.core.job_records.schemas import JobRecordOut
from pickwise.core.profile import service as profile
from pickwise.core.profile.schemas import (
    AddressOut,
    AddressSet,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    DocumentOut,
    PersonalOut,
    SelfContactUpdate,
)
from pickwise.platform import audit
from pickwise.platform.auth.dependencies import DB, Authorized, require
from pickwise.platform.scopes import DataScope, ScopeType

router = APIRouter(prefix="/v1/me/employee", tags=["my employee record"])

Read = Annotated[Authorized, Depends(require("core.me.read"))]
Write = Annotated[Authorized, Depends(require("core.me.update"))]
Row = dict[str, Any]
SELF = (DataScope(ScopeType.SELF),)


async def _me(db: DB, auth: Authorized) -> uuid.UUID:
    return await employees.employee_of_membership(db, auth.principal.membership_id)


@router.get("", response_model=EmployeeOut)
async def my_record(db: DB, auth: Read) -> Row:
    employee_id = await _me(db, auth)
    return employees.present(await employees.get(db, auth.principal, SELF, employee_id))


@router.get("/personal", response_model=PersonalOut)
async def my_personal(db: DB, auth: Read) -> Row:
    return await profile.get_personal(db, await _me(db, auth))


@router.put("/contact-details", response_model=PersonalOut)
async def change_my_contact_details(body: SelfContactUpdate, db: DB, auth: Write) -> Row:
    """Change your personal email and phone."""
    employee_id = await _me(db, auth)
    await profile.set_contact_details(db, employee_id, body.model_dump())
    await audit.record(db, "employee.self_contact_changed", "core.employees", employee_id)
    return await profile.get_personal(db, employee_id)


@router.get("/job-records", response_model=list[JobRecordOut])
async def my_jobs(db: DB, auth: Read) -> list[Row]:
    return await jobs.list_records(db, await _me(db, auth))


@router.get("/addresses", response_model=list[AddressOut])
async def my_addresses(db: DB, auth: Read) -> list[Row]:
    return await profile.list_addresses(db, await _me(db, auth))


@router.put("/addresses/{address_type}", response_model=AddressOut)
async def set_my_address(
    address_type: Literal["current", "permanent"], body: AddressSet, db: DB, auth: Write
) -> Row:
    employee_id = await _me(db, auth)
    row = await profile.set_address(
        db, employee_id, address_type, body.model_dump(exclude={"valid_from"}), body.valid_from
    )
    await audit.record(db, "employee.self_address_changed", "core.employees", employee_id)
    return row


@router.get("/emergency-contacts", response_model=list[ContactOut])
async def my_contacts(db: DB, auth: Read) -> list[Row]:
    return await profile.list_for(db, profile.EMERGENCY, await _me(db, auth))


@router.post("/emergency-contacts", response_model=ContactOut, status_code=201)
async def add_my_contact(body: ContactCreate, db: DB, auth: Write) -> Row:
    employee_id = await _me(db, auth)
    row = await profile.add_contact(db, employee_id, body.model_dump())
    await audit.record(db, "employee.self_contact_added", "core.employees", employee_id)
    return row


@router.put("/emergency-contacts/{contact_id}", response_model=ContactOut)
async def edit_my_contact(contact_id: uuid.UUID, body: ContactUpdate, db: DB, auth: Write) -> Row:
    employee_id = await _me(db, auth)
    return await profile.edit_contact(
        db, employee_id, contact_id, body.model_dump(exclude={"row_version"}), body.row_version
    )


@router.delete("/emergency-contacts/{contact_id}", status_code=204)
async def delete_my_contact(contact_id: uuid.UUID, db: DB, auth: Write) -> None:
    await profile.remove(db, profile.EMERGENCY, await _me(db, auth), contact_id)


@router.get("/identity", response_model=list[IdentityOut])
async def my_identity(db: DB, auth: Read) -> list[Row]:
    """Your identity documents, masked."""
    return await identity.list_identity(db, await _me(db, auth))


@router.get("/bank-accounts", response_model=list[BankOut])
async def my_bank_accounts(db: DB, auth: Read) -> list[Row]:
    """Your bank accounts, masked."""
    return await identity.list_bank(db, await _me(db, auth))


@router.get("/documents", response_model=list[DocumentOut])
async def my_documents(db: DB, auth: Read) -> list[Row]:
    """Documents HR has shared with you."""
    return await profile.list_for(
        db, profile.DOCUMENT, await _me(db, auth), where="visible_to_employee"
    )
