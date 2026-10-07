# SPDX-License-Identifier: AGPL-3.0-only
"""Identity documents and bank accounts (/v1/employees/{id}/identity, /bank).

Numbers go in and are encrypted at once; they never come back. Responses show the last four
characters, and the full number comes only from ``POST /v1/pii/reveal`` (permission
``core.employee.*.reveal``, audited). Each call checks the employee is within the caller's data
scope for that permission and answers 404 for anyone outside it.
"""

import json
import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from pickwise.core.deps import Keys
from pickwise.core.employees import service as employees
from pickwise.core.identity import service
from pickwise.core.identity.schemas import (
    BankCreate,
    BankOut,
    IdentityCreate,
    IdentityOut,
    IdentityVerify,
)
from pickwise.platform import audit
from pickwise.platform.auth.dependencies import DB, Authorized, require
from pickwise.platform.idempotency import idempotent

router = APIRouter(prefix="/v1/employees/{employee_id}", tags=["employee identity and bank"])

IdentityRead = Annotated[Authorized, Depends(require("core.employee.identity.read"))]
IdentityWrite = Annotated[Authorized, Depends(require("core.employee.identity.update"))]
BankRead = Annotated[Authorized, Depends(require("core.employee.bank.read"))]
BankWrite = Annotated[Authorized, Depends(require("core.employee.bank.update"))]
Row = dict[str, Any]


def _json(row: Row) -> Row:
    def default(value: Any) -> Any:
        return str(value) if isinstance(value, uuid.UUID | Decimal) else value.isoformat()

    parsed: Row = json.loads(json.dumps(row, default=default))
    return parsed


async def _scope(db: DB, auth: Authorized, employee_id: uuid.UUID) -> None:
    await employees.get(db, auth.principal, auth.scopes, employee_id)


@router.get("/identity", response_model=list[IdentityOut])
async def list_identity(
    employee_id: uuid.UUID,
    db: DB,
    auth: IdentityRead,
    history: Annotated[bool, Query(description="Include replaced documents.")] = False,
) -> list[Row]:
    await _scope(db, auth, employee_id)
    return await service.list_identity(db, employee_id, history=history)


@router.post("/identity", response_model=IdentityOut, status_code=201)
async def add_identity(
    employee_id: uuid.UUID,
    body: IdentityCreate,
    request: Request,
    response: Response,
    db: DB,
    auth: IdentityWrite,
    keys: Keys,
) -> Any:
    """Record an identity document. It replaces the current one of the same type, which stays
    as history. A number already on file for another employee is refused (409), without saying
    whose it is."""
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    await _scope(db, auth, employee_id)
    row = await service.add_identity(db, keys, auth.tenant_id, employee_id, body.model_dump())
    await audit.record(
        db,
        "employee.identity_added",
        "core.identity_documents",
        row["id"],
        {"employee_id": str(employee_id), "doc_type": body.doc_type},
    )
    payload = _json(row)
    await idem.finish(status.HTTP_201_CREATED, payload)
    response.status_code = status.HTTP_201_CREATED
    return payload


@router.post("/identity/{document_id}/verify", response_model=IdentityOut)
async def verify_identity(
    employee_id: uuid.UUID,
    document_id: uuid.UUID,
    body: IdentityVerify,
    db: DB,
    auth: IdentityWrite,
) -> Row:
    await _scope(db, auth, employee_id)
    row = await service.verify_identity(
        db, employee_id, document_id, body.status, body.row_version, auth.principal.user_id
    )
    await audit.record(
        db,
        "employee.identity_verified",
        "core.identity_documents",
        document_id,
        {"status": body.status},
    )
    return row


@router.get("/bank-accounts", response_model=list[BankOut])
async def list_bank(employee_id: uuid.UUID, db: DB, auth: BankRead) -> list[Row]:
    await _scope(db, auth, employee_id)
    return await service.list_bank(db, employee_id)


@router.post("/bank-accounts", response_model=BankOut, status_code=201)
async def add_bank(
    employee_id: uuid.UUID,
    body: BankCreate,
    request: Request,
    response: Response,
    db: DB,
    auth: BankWrite,
    keys: Keys,
) -> Any:
    """Add a bank account. A primary one takes over from its start date; the previous primary
    ends the day before. The number is masked afterwards."""
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    await _scope(db, auth, employee_id)
    row = await service.add_bank(db, keys, auth.tenant_id, employee_id, body.model_dump())
    await audit.record(
        db,
        "employee.bank_account_added",
        "core.bank_accounts",
        row["id"],
        {"employee_id": str(employee_id)},
    )
    payload = _json(row)
    await idem.finish(status.HTTP_201_CREATED, payload)
    response.status_code = status.HTTP_201_CREATED
    return payload


@router.post("/bank-accounts/{account_id}/end", response_model=BankOut)
async def end_bank_account(
    employee_id: uuid.UUID,
    account_id: uuid.UUID,
    row_version: Annotated[int, Query(ge=1)],
    db: DB,
    auth: BankWrite,
) -> Row:
    """Stop using an account from today. Accounts are never deleted: past pay went to them."""
    await _scope(db, auth, employee_id)
    row = await service.end_bank(db, employee_id, account_id, row_version)
    await audit.record(
        db,
        "employee.bank_account_ended",
        "core.bank_accounts",
        account_id,
        {"employee_id": str(employee_id)},
    )
    return row
