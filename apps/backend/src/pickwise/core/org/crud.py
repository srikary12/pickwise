# SPDX-License-Identifier: AGPL-3.0-only
"""Plain create / read / update / archive for the org tables, driven by a table spec.

The tables share one shape (id, row_version, archived_at, a unique code), so one audited code
path serves them all. Column names come from the spec, never from a request.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.shared.errors import ConflictError, NotFoundError, UnprocessableError

UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
EXCLUSION_VIOLATION = "23P01"


@dataclass(frozen=True, slots=True)
class PgError:
    sqlstate: str | None
    constraint: str | None


def pg_error(exc: IntegrityError) -> PgError:
    """The SQLSTATE and constraint behind an IntegrityError (asyncpg keeps them on __cause__)."""
    orig: Any = exc.orig
    cause: Any = getattr(orig, "__cause__", None) or orig
    return PgError(
        getattr(cause, "sqlstate", None) or getattr(orig, "sqlstate", None),
        getattr(cause, "constraint_name", None),
    )


@dataclass(frozen=True, slots=True)
class Spec:
    table: str  # schema-qualified
    noun: str  # "location", for messages
    columns: tuple[str, ...]  # everything returned, id first
    writable: tuple[str, ...]
    order_by: str
    jsonb: frozenset[str] = frozenset()
    # Message for a foreign key that points at nothing, by column.
    references: dict[str, str] = field(default_factory=dict)


def _select(spec: Spec) -> str:
    return ", ".join(spec.columns)


async def list_rows(
    db: AsyncSession,
    spec: Spec,
    *,
    include_archived: bool = False,
    where: str = "true",
    **params: Any,
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                f"SELECT {_select(spec)} FROM {spec.table} "  # noqa: S608
                f"WHERE ({where}) AND (:archived OR archived_at IS NULL) ORDER BY {spec.order_by}"
            ),
            {"archived": include_archived, **params},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def get_row(db: AsyncSession, spec: Spec, row_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                text(f"SELECT {_select(spec)} FROM {spec.table} WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFoundError(f"{spec.noun.capitalize()} not found.")
    return dict(row)


def _bind(spec: Spec, statement: str) -> Any:
    clause = text(statement)
    for name in spec.jsonb:
        clause = clause.bindparams(bindparam(name, type_=JSONB))
    return clause


def _translate(spec: Spec, exc: IntegrityError) -> Exception:
    error = pg_error(exc)
    if error.sqlstate == UNIQUE_VIOLATION:
        return ConflictError(
            f"A {spec.noun} with that code or name already exists.", code="duplicate"
        )
    if error.sqlstate == FOREIGN_KEY_VIOLATION:
        for column, message in spec.references.items():
            if error.constraint and column in error.constraint:
                return UnprocessableError(message, code="unknown_reference")
        return UnprocessableError("A referenced record doesn't exist.", code="unknown_reference")
    if error.sqlstate == CHECK_VIOLATION:
        return UnprocessableError(f"That {spec.noun} has an invalid value.", code="invalid")
    return exc


async def insert_row(db: AsyncSession, spec: Spec, values: dict[str, Any]) -> dict[str, Any]:
    names = [c for c in spec.writable if c in values]
    statement = (
        f"INSERT INTO {spec.table} ({', '.join(names)}) "  # noqa: S608
        f"VALUES ({', '.join(':' + n for n in names)}) RETURNING {_select(spec)}"
    )
    try:
        async with db.begin_nested():
            row = (await db.execute(_bind(spec, statement), values)).mappings().one()
    except IntegrityError as exc:
        raise _translate(spec, exc) from exc
    return dict(row)


async def update_row(
    db: AsyncSession, spec: Spec, row_id: uuid.UUID, values: dict[str, Any], row_version: int
) -> dict[str, Any]:
    names = [c for c in spec.writable if c in values]
    assignments = ", ".join(f"{n} = :{n}" for n in names)
    statement = (
        f"UPDATE {spec.table} SET {assignments} "  # noqa: S608
        f"WHERE id = :_id AND row_version = :_v RETURNING {_select(spec)}"
    )
    try:
        async with db.begin_nested():
            result = await db.execute(
                _bind(spec, statement), {**values, "_id": row_id, "_v": row_version}
            )
            row = result.mappings().one_or_none()
    except IntegrityError as exc:
        raise _translate(spec, exc) from exc
    if row is None:
        await get_row(db, spec, row_id)  # 404 if it doesn't exist at all
        raise ConflictError(
            f"Someone else changed this {spec.noun}. Reload and try again.",
            code="stale_row_version",
        )
    return dict(row)


async def set_archived(
    db: AsyncSession, spec: Spec, row_id: uuid.UUID, archived: bool
) -> dict[str, Any]:
    current = await get_row(db, spec, row_id)
    if archived == (current["archived_at"] is not None):
        return current
    try:
        async with db.begin_nested():
            row = (
                (
                    await db.execute(
                        text(
                            f"UPDATE {spec.table} "  # noqa: S608
                            "SET archived_at = CASE WHEN :a THEN now() END "
                            f"WHERE id = :id RETURNING {_select(spec)}"
                        ),
                        {"a": archived, "id": row_id},
                    )
                )
                .mappings()
                .one()
            )
    except IntegrityError as exc:
        raise _translate(spec, exc) from exc
    return dict(row)
