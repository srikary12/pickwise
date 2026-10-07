# SPDX-License-Identifier: AGPL-3.0-only
"""Identity documents and bank accounts: restricted data (CLAUDE.md rule 13, ADR 0024).

Numbers are encrypted at once with the tenant's data key (the AAD binds each ciphertext to its
table, column, tenant and row) and indexed with an HMAC under the tenant's blind-index key, so a
duplicate is found without decrypting anything. Reads return only the last four characters; the
full number comes from the audited reveal endpoint.

Aadhaar defaults to ``last4`` mode (tenant setting ``aadhaar_mode``): only the last four digits are
kept and nothing else, not even a hash. Uniqueness for Aadhaar only exists in ``full`` mode.
During a blind-index key rotation a duplicate check looks under every non-retired key.
"""

import datetime
import re
import uuid
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, DATERANGE, Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.core.crud import UNIQUE_VIOLATION, pg_error
from pickwise.platform.crypto import (
    BLIND_INDEX,
    Keyring,
    blind_index,
    field_aad,
    normalize_identifier,
)
from pickwise.shared.errors import ConflictError, NotFoundError, UnprocessableError
from pickwise.shared.ids import uuid7

IDENTITY_TABLE = "core.identity_documents"
BANK_TABLE = "core.bank_accounts"

_FORMATS = {
    "pan": (re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$"), "A PAN looks like AABCA1234F."),
    "aadhaar": (re.compile(r"^[2-9][0-9]{11}$"), "An Aadhaar number has 12 digits."),
    "passport": (re.compile(r"^[A-Z][0-9]{7}$"), "A passport number looks like K1234567."),
    "uan": (re.compile(r"^[0-9]{12}$"), "A UAN has 12 digits."),
    "esic_ip": (re.compile(r"^([0-9]{10}|[0-9]{17})$"), "An ESIC number has 10 or 17 digits."),
    "voter_id": (re.compile(r"^[A-Z]{3}[0-9]{7}$"), "A voter ID looks like ABC1234567."),
    "driving_licence": (re.compile(r"^[A-Z0-9]{8,20}$"), "Check the licence number."),
    "visa": (re.compile(r"^[A-Z0-9]{5,20}$"), "Check the visa number."),
}
# Doc types whose number is encrypted and blind-indexed (all except Aadhaar in last4 mode).
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_valid(number: str) -> bool:
    """Aadhaar's check digit (the Verhoeff algorithm)."""
    check = 0
    for i, digit in enumerate(reversed(number)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][int(digit)]]
    return check == 0


def normalise(doc_type: str, value: str) -> str:
    return normalize_identifier(value)


async def aadhaar_mode(db: AsyncSession) -> str:
    mode: str | None = (
        await db.execute(
            text(
                "SELECT settings->>'aadhaar_mode' FROM platform.tenants "
                "WHERE id = platform.current_tenant_id()"
            )
        )
    ).scalar_one_or_none()
    return mode if mode in ("last4", "full") else "last4"


def _bidx_in(db: AsyncSession, keyring: Keyring, value: str) -> list[bytes]:
    return keyring.blind_indexes(value)


async def _duplicate_exists(
    db: AsyncSession,
    table: str,
    column: str,
    keyring: Keyring,
    value: str,
    employee_id: uuid.UUID,
    extra: str = "true",
) -> bool:
    statement = text(
        f"SELECT 1 FROM {table} WHERE {column} = ANY(:b) "  # noqa: S608
        f"AND employee_id <> :e AND ({extra}) LIMIT 1"
    ).bindparams(bindparam("b", type_=ARRAY(BYTEA)))
    found = (
        await db.execute(statement, {"b": keyring.blind_indexes(value), "e": employee_id})
    ).first()
    return found is not None


# --- identity documents ------------------------------------------------------------------------

_IDENTITY_COLUMNS = (
    "id, employee_id, doc_type, value_last4 AS last4, (value_enc IS NOT NULL) AS has_value, "
    "name_as_per_doc, issued_on, expires_on, is_current, file_id, verification_status, "
    "verified_at, row_version"
)


async def list_identity(
    db: AsyncSession, employee_id: uuid.UUID, *, history: bool = False
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                f"SELECT {_IDENTITY_COLUMNS} FROM core.identity_documents "  # noqa: S608
                "WHERE employee_id = :e AND (:h OR is_current) ORDER BY doc_type, created_at DESC"
            ),
            {"e": employee_id, "h": history},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def add_identity(
    db: AsyncSession,
    keyring: Keyring,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    values: dict[str, Any],
) -> dict[str, Any]:
    doc_type: str = values["doc_type"]
    value = normalise(doc_type, values["value"])
    pattern, message = _FORMATS[doc_type]
    store_full = True
    if doc_type == "aadhaar":
        mode = await aadhaar_mode(db)
        if mode == "full":
            store_full = True
        else:
            store_full = False
            # Last4 mode accepts the whole number (checked, then discarded) or just four digits.
            if re.fullmatch(r"[0-9]{4}", value):
                pattern = re.compile(r"^[0-9]{4}$")
    if not pattern.match(value):
        raise UnprocessableError(message, code="invalid_identity")
    if doc_type == "aadhaar" and len(value) == 12 and not verhoeff_valid(value):
        raise UnprocessableError("That Aadhaar number isn't valid.", code="invalid_identity")
    if doc_type == "aadhaar" and store_full and len(value) != 12:
        raise UnprocessableError("An Aadhaar number has 12 digits.", code="invalid_identity")

    document_id = uuid7()
    enc = bidx = None
    key_version = None
    if store_full:
        if await _duplicate_exists(
            db,
            IDENTITY_TABLE,
            "value_bidx",
            keyring,
            value,
            employee_id,
            "is_current AND doc_type = '" + doc_type + "'",
        ):
            raise ConflictError(
                "That number is already recorded for another employee.",
                code="duplicate_identity",
            )
        aad = field_aad(IDENTITY_TABLE, "value_enc", tenant_id, document_id)
        enc = keyring.encrypt(value.encode(), aad)
        active = keyring.active_key(BLIND_INDEX)
        bidx = blind_index(active.material, value)
        key_version = active.version

    # The current document of this type becomes history before the new one is inserted.
    superseded: uuid.UUID | None = (
        await db.execute(
            text(
                "UPDATE core.identity_documents SET is_current = false "
                "WHERE employee_id = :e AND doc_type = :t AND is_current RETURNING id"
            ),
            {"e": employee_id, "t": doc_type},
        )
    ).scalar_one_or_none()
    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO core.identity_documents (id, employee_id, doc_type, value_enc, "
                    "  value_last4, value_bidx, bidx_key_version, name_as_per_doc, issued_on, "
                    "  expires_on, file_id) "
                    "VALUES (:id, :e, :t, :enc, :l4, :bidx, :kv, :name, :issued, :expires, :file)"
                ),
                {
                    "id": document_id,
                    "e": employee_id,
                    "t": doc_type,
                    "enc": enc,
                    "l4": value[-4:],
                    "bidx": bidx,
                    "kv": key_version,
                    "name": values.get("name_as_per_doc"),
                    "issued": values.get("issued_on"),
                    "expires": values.get("expires_on"),
                    "file": values.get("file_id"),
                },
            )
    except IntegrityError as exc:
        if pg_error(exc).sqlstate == UNIQUE_VIOLATION:
            raise ConflictError(
                "That number is already recorded for another employee.",
                code="duplicate_identity",
            ) from exc
        raise
    if superseded is not None:
        await db.execute(
            text("UPDATE core.identity_documents SET superseded_by_id = :n WHERE id = :o"),
            {"n": document_id, "o": superseded},
        )
    return await get_identity(db, employee_id, document_id)


async def get_identity(
    db: AsyncSession, employee_id: uuid.UUID, document_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                text(
                    f"SELECT {_IDENTITY_COLUMNS} FROM core.identity_documents "  # noqa: S608
                    "WHERE id = :id AND employee_id = :e"
                ),
                {"id": document_id, "e": employee_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFoundError("Identity document not found.")
    return dict(row)


async def verify_identity(
    db: AsyncSession,
    employee_id: uuid.UUID,
    document_id: uuid.UUID,
    status: str,
    row_version: int,
    user_id: uuid.UUID | None,
) -> dict[str, Any]:
    await get_identity(db, employee_id, document_id)
    updated = (
        await db.execute(
            text(
                "UPDATE core.identity_documents SET verification_status = :s, verified_by = :u, "
                "  verified_at = now() WHERE id = :id AND row_version = :v RETURNING id"
            ),
            {"s": status, "u": user_id, "id": document_id, "v": row_version},
        )
    ).first()
    if updated is None:
        raise ConflictError(
            "Someone else changed this. Reload and try again.", code="stale_row_version"
        )
    return await get_identity(db, employee_id, document_id)


async def reveal_identity(
    db: AsyncSession, keyring: Keyring, tenant_id: uuid.UUID, document_id: uuid.UUID, visible: Any
) -> str:
    """Decrypt one document's number. ``visible`` is the caller's data-scope condition on the
    owning employee; a document outside it is a 404."""
    from sqlalchemy import column, select, table

    docs = table(
        "identity_documents",
        column("id"),
        column("employee_id"),
        column("value_enc"),
        schema="core",
    )
    row = (
        await db.execute(
            select(docs.c.value_enc).where(docs.c.id == document_id, visible(docs.c.employee_id))
        )
    ).first()
    if row is None:
        raise NotFoundError("Identity document not found.")
    if row[0] is None:
        raise NotFoundError(
            "Only the last four digits are stored for that number.", code="not_stored"
        )
    aad = field_aad(IDENTITY_TABLE, "value_enc", tenant_id, document_id)
    return keyring.decrypt(bytes(row[0]), aad).decode()


# --- bank accounts -----------------------------------------------------------------------------

_BANK_COLUMNS = (
    "id, employee_id, account_holder_name, account_last4 AS last4, ifsc, bank_name, account_type, "
    "is_primary, lower(valid_during) AS valid_from, "
    "CASE WHEN upper_inf(valid_during) THEN NULL ELSE upper(valid_during) - 1 END AS valid_to, "
    "verification_status, row_version"
)
_RANGE = bindparam("period", type_=DATERANGE)


async def list_bank(db: AsyncSession, employee_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                f"SELECT {_BANK_COLUMNS} FROM core.bank_accounts "  # noqa: S608
                "WHERE employee_id = :e ORDER BY valid_during DESC, created_at DESC"
            ),
            {"e": employee_id},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def add_bank(
    db: AsyncSession,
    keyring: Keyring,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    values: dict[str, Any],
) -> dict[str, Any]:
    today: datetime.date = (await db.execute(text("SELECT core.today()"))).scalar_one()
    start: datetime.date = values.get("valid_from") or today
    number = values["account_number"]
    account_id = uuid7()
    aad = field_aad(BANK_TABLE, "account_number_enc", tenant_id, account_id)
    active = keyring.active_key(BLIND_INDEX)
    if values["is_primary"]:
        # The primary account in force on the start date ends the day before; any that would
        # begin later are dropped, so the new one is the only primary from the start date on.
        previous = (
            await db.execute(
                text(
                    "SELECT id, lower(valid_during) FROM core.bank_accounts "
                    "WHERE employee_id = :e AND is_primary AND valid_during @> CAST(:d AS date)"
                ),
                {"e": employee_id, "d": start},
            )
        ).first()
        if previous is not None:
            if previous[1] >= start:
                await db.execute(
                    text("DELETE FROM core.bank_accounts WHERE id = :id"), {"id": previous[0]}
                )
            else:
                await db.execute(
                    text(
                        "UPDATE core.bank_accounts SET valid_during = :period WHERE id = :id"
                    ).bindparams(_RANGE),
                    {"id": previous[0], "period": Range(previous[1], start, bounds="[)")},
                )
        await db.execute(
            text(
                "DELETE FROM core.bank_accounts WHERE employee_id = :e AND is_primary "
                "AND lower(valid_during) > :d"
            ),
            {"e": employee_id, "d": start},
        )
    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO core.bank_accounts (id, employee_id, account_holder_name, "
                    "  account_number_enc, account_last4, account_bidx, bidx_key_version, ifsc, "
                    "  bank_name, account_type, is_primary, valid_during) "
                    "VALUES (:id, :e, :holder, :enc, :l4, :bidx, :kv, :ifsc, :bank, :type, "
                    "  :primary, :period)"
                ).bindparams(_RANGE),
                {
                    "id": account_id,
                    "e": employee_id,
                    "holder": values["account_holder_name"],
                    "enc": keyring.encrypt(number.encode(), aad),
                    "l4": number[-4:],
                    "bidx": blind_index(active.material, number),
                    "kv": active.version,
                    "ifsc": values["ifsc"],
                    "bank": values["bank_name"],
                    "type": values["account_type"],
                    "primary": values["is_primary"],
                    "period": Range(start, None, bounds="[)"),
                },
            )
    except IntegrityError as exc:
        raise ConflictError(
            "This employee already has a primary account for that period.", code="primary_overlap"
        ) from exc
    return await get_bank(db, employee_id, account_id)


async def get_bank(
    db: AsyncSession, employee_id: uuid.UUID, account_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                text(
                    f"SELECT {_BANK_COLUMNS} FROM core.bank_accounts "  # noqa: S608
                    "WHERE id = :id AND employee_id = :e"
                ),
                {"id": account_id, "e": employee_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFoundError("Bank account not found.")
    return dict(row)


async def end_bank(
    db: AsyncSession, employee_id: uuid.UUID, account_id: uuid.UUID, row_version: int
) -> dict[str, Any]:
    """Stop using an account from today (never deletes it: payslips already paid to it)."""
    account = await get_bank(db, employee_id, account_id)
    today: datetime.date = (await db.execute(text("SELECT core.today()"))).scalar_one()
    if account["valid_to"] is not None and account["valid_to"] < today:
        return account
    start: datetime.date = account["valid_from"]
    end = max(today, start + datetime.timedelta(days=1))
    updated = (
        await db.execute(
            text(
                "UPDATE core.bank_accounts SET valid_during = :period "
                "WHERE id = :id AND row_version = :v RETURNING id"
            ).bindparams(_RANGE),
            {"id": account_id, "v": row_version, "period": Range(start, end, bounds="[)")},
        )
    ).first()
    if updated is None:
        raise ConflictError(
            "Someone else changed this. Reload and try again.", code="stale_row_version"
        )
    return await get_bank(db, employee_id, account_id)


async def reveal_bank(
    db: AsyncSession, keyring: Keyring, tenant_id: uuid.UUID, account_id: uuid.UUID, visible: Any
) -> str:
    from sqlalchemy import column, select, table

    accounts = table(
        "bank_accounts",
        column("id"),
        column("employee_id"),
        column("account_number_enc"),
        schema="core",
    )
    row = (
        await db.execute(
            select(accounts.c.account_number_enc).where(
                accounts.c.id == account_id, visible(accounts.c.employee_id)
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("Bank account not found.")
    aad = field_aad(BANK_TABLE, "account_number_enc", tenant_id, account_id)
    return keyring.decrypt(bytes(row[0]), aad).decode()
