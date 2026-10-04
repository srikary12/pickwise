# SPDX-License-Identifier: AGPL-3.0-only
"""Definitions and payload validation. Modules call ``validate_custom_fields`` in their
service layer before storing an entity's ``custom_fields`` jsonb (DATA_MODEL §0)."""

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.shared.errors import ConflictError, NotFoundError, UnprocessableError

ENTITY_TYPES = ("employee", "candidate", "job", "application", "requisition")
FIELD_TYPES = ("text", "number", "date", "boolean", "select", "multiselect", "user", "file")
MAX_TEXT = 2000
MAX_CHOICES = 100

_COLUMNS = (
    "id, entity_type, key, label, field_type, options, required, is_sensitive, position, "
    "archived_at, row_version"
)


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    id: uuid.UUID
    entity_type: str
    key: str
    label: str
    field_type: str
    options: dict[str, Any]
    required: bool
    is_sensitive: bool
    position: int
    archived_at: datetime.datetime | None
    row_version: int


def validate_options(field_type: str, options: dict[str, Any]) -> dict[str, Any]:
    """Check the ``options`` shape for a field type and return the normalised options."""
    allowed: dict[str, set[str]] = {
        "text": {"max_length"},
        "number": {"min", "max", "integer"},
        "date": {"min", "max"},
        "select": {"choices"},
        "multiselect": {"choices"},
    }
    extra = set(options) - allowed.get(field_type, set())
    if extra:
        raise UnprocessableError(
            f"Unknown options for a {field_type} field: {', '.join(sorted(extra))}.",
            code="invalid_options",
        )
    if field_type in ("select", "multiselect"):
        choices = options.get("choices")
        if not isinstance(choices, list) or not 1 <= len(choices) <= MAX_CHOICES:
            raise UnprocessableError(
                f"Give between 1 and {MAX_CHOICES} choices.", code="invalid_options"
            )
        values = []
        for choice in choices:
            if (
                not isinstance(choice, dict)
                or not isinstance(choice.get("value"), str)
                or not choice["value"]
                or not isinstance(choice.get("label"), str)
            ):
                raise UnprocessableError(
                    'Each choice needs a "value" and a "label".', code="invalid_options"
                )
            values.append(choice["value"])
        if len(set(values)) != len(values):
            raise UnprocessableError("Choice values must be unique.", code="invalid_options")
    if field_type == "text" and "max_length" in options:
        limit = options["max_length"]
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_TEXT:
            raise UnprocessableError(
                f"max_length must be between 1 and {MAX_TEXT}.", code="invalid_options"
            )
    if field_type == "number":
        for bound in ("min", "max"):
            if bound in options and not _is_number(options[bound]):
                raise UnprocessableError(f"{bound} must be a number.", code="invalid_options")
        if "integer" in options and not isinstance(options["integer"], bool):
            raise UnprocessableError("integer must be true or false.", code="invalid_options")
        low, high = options.get("min"), options.get("max")
        if low is not None and high is not None and low > high:
            raise UnprocessableError("min can't exceed max.", code="invalid_options")
    if field_type == "date":
        for bound in ("min", "max"):
            if bound in options:
                _parse_date(options[bound], f"options.{bound}")
    return options


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _parse_date(value: Any, label: str) -> datetime.date:
    try:
        if not isinstance(value, str):
            raise ValueError
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise UnprocessableError(
            f"{label} must be an ISO date (YYYY-MM-DD).", code="invalid_options"
        ) from None


async def list_definitions(
    db: AsyncSession, entity_type: str | None = None, *, include_archived: bool = False
) -> list[FieldDefinition]:
    rows = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM platform.custom_field_definitions "  # noqa: S608
                "WHERE (CAST(:et AS text) IS NULL OR entity_type = :et) "
                "  AND (:archived OR archived_at IS NULL) "
                "ORDER BY entity_type, position, key"
            ),
            {"et": entity_type, "archived": include_archived},
        )
    ).all()
    return [FieldDefinition(*r) for r in rows]


async def get_definition(db: AsyncSession, definition_id: uuid.UUID) -> FieldDefinition:
    row = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM platform.custom_field_definitions "  # noqa: S608
                "WHERE id = :id"
            ),
            {"id": definition_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Custom field not found.")
    return FieldDefinition(*row)


async def create_definition(
    db: AsyncSession,
    *,
    entity_type: str,
    key: str,
    label: str,
    field_type: str,
    options: dict[str, Any],
    required: bool,
    is_sensitive: bool,
    position: int,
) -> FieldDefinition:
    validate_options(field_type, options)
    row = (
        await db.execute(
            text(
                "INSERT INTO platform.custom_field_definitions (entity_type, key, label, "  # noqa: S608
                "  field_type, options, required, is_sensitive, position) "
                "VALUES (:et, :key, :label, :ft, :options, :required, :sensitive, :pos) "
                "ON CONFLICT (tenant_id, entity_type, key) WHERE archived_at IS NULL DO NOTHING "
                f"RETURNING {_COLUMNS}"
            ).bindparams(bindparam("options", type_=JSONB)),
            {
                "et": entity_type,
                "key": key,
                "label": label,
                "ft": field_type,
                "options": options,
                "required": required,
                "sensitive": is_sensitive,
                "pos": position,
            },
        )
    ).one_or_none()
    if row is None:
        raise ConflictError("A field with that key already exists.", code="duplicate_key")
    return FieldDefinition(*row)


async def update_definition(
    db: AsyncSession,
    definition_id: uuid.UUID,
    *,
    row_version: int,
    label: str,
    options: dict[str, Any],
    required: bool,
    is_sensitive: bool,
    position: int,
) -> FieldDefinition:
    """The key and type are fixed: changing them would invalidate stored values."""
    current = await get_definition(db, definition_id)
    validate_options(current.field_type, options)
    row = (
        await db.execute(
            text(
                "UPDATE platform.custom_field_definitions SET label = :label, options = :options, "  # noqa: S608
                "  required = :required, is_sensitive = :sensitive, position = :pos "
                f"WHERE id = :id AND row_version = :v RETURNING {_COLUMNS}"
            ).bindparams(bindparam("options", type_=JSONB)),
            {
                "label": label,
                "options": options,
                "required": required,
                "sensitive": is_sensitive,
                "pos": position,
                "id": definition_id,
                "v": row_version,
            },
        )
    ).one_or_none()
    if row is None:
        raise ConflictError(
            "Someone else changed this field. Reload and try again.", code="stale_row_version"
        )
    return FieldDefinition(*row)


async def set_archived(
    db: AsyncSession, definition_id: uuid.UUID, archived: bool
) -> FieldDefinition:
    current = await get_definition(db, definition_id)
    if archived == (current.archived_at is not None):
        return current
    try:
        row = (
            await db.execute(
                text(
                    "UPDATE platform.custom_field_definitions "  # noqa: S608
                    "SET archived_at = CASE WHEN :a THEN now() END "
                    f"WHERE id = :id RETURNING {_COLUMNS}"
                ),
                {"a": archived, "id": definition_id},
            )
        ).one()
    except IntegrityError as exc:  # an active field took the key meanwhile
        raise ConflictError("An active field with that key exists.", code="duplicate_key") from exc
    return FieldDefinition(*row)


# --- payload validation ---------------------------------------------------------------------


class _Collector:
    def __init__(self) -> None:
        self.errors: dict[str, str] = {}

    def add(self, key: str, message: str) -> None:
        self.errors.setdefault(key, message)


async def _active_user_ids(db: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    rows = (
        await db.execute(
            text(
                "SELECT user_id FROM platform.memberships "
                "WHERE user_id = ANY(:ids) AND status = 'active'"
            ),
            {"ids": ids},
        )
    ).all()
    return {r[0] for r in rows}


async def _usable_file_ids(db: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    rows = (
        await db.execute(
            text(
                "SELECT id FROM platform.files WHERE id = ANY(:ids) "
                "AND scan_status IN ('pending', 'clean') AND purged_at IS NULL"
            ),
            {"ids": ids},
        )
    ).all()
    return {r[0] for r in rows}


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _check_scalar(definition: FieldDefinition, value: Any) -> tuple[Any, str | None]:
    """Validate a non-reference value; returns (normalised value, error message)."""
    options = definition.options
    match definition.field_type:
        case "text":
            if not isinstance(value, str):
                return value, "must be text"
            limit = int(options.get("max_length", MAX_TEXT))
            trimmed = value.strip()
            if len(trimmed) > limit:
                return value, f"must be at most {limit} characters"
            return trimmed, None
        case "number":
            if not _is_number(value):
                return value, "must be a number"
            if options.get("integer") and int(value) != value:
                return value, "must be a whole number"
            if "min" in options and value < options["min"]:
                return value, f"must be at least {options['min']}"
            if "max" in options and value > options["max"]:
                return value, f"must be at most {options['max']}"
            return value, None
        case "date":
            try:
                parsed = datetime.date.fromisoformat(value) if isinstance(value, str) else None
            except ValueError:
                parsed = None
            if parsed is None:
                return value, "must be a date (YYYY-MM-DD)"
            if "min" in options and parsed < datetime.date.fromisoformat(options["min"]):
                return value, f"must be on or after {options['min']}"
            if "max" in options and parsed > datetime.date.fromisoformat(options["max"]):
                return value, f"must be on or before {options['max']}"
            return parsed.isoformat(), None
        case "boolean":
            return value, None if isinstance(value, bool) else "must be true or false"
        case "select":
            choices = {c["value"] for c in options.get("choices", [])}
            return value, None if value in choices else "isn't one of the choices"
        case "multiselect":
            choices = {c["value"] for c in options.get("choices", [])}
            if not isinstance(value, list) or len(set(map(str, value))) != len(value):
                return value, "must be a list of distinct choices"
            if not set(value) <= choices:
                return value, "has a value that isn't one of the choices"
            return value, None
    return value, None


async def validate_custom_fields(
    db: AsyncSession,
    entity_type: str,
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    can_edit_sensitive: bool = False,
) -> dict[str, Any]:
    """Validate and normalise an entity's ``custom_fields`` payload.

    ``payload`` is the full new value. ``existing`` is what is stored now (None on create);
    values of archived fields are kept only if unchanged, and sensitive fields can only be
    changed by a caller with ``can_edit_sensitive``. Raises 422 listing every problem.
    """
    existing = existing or {}
    definitions = await list_definitions(db, entity_type, include_archived=True)
    by_key = {d.key: d for d in definitions}
    problems = _Collector()
    cleaned: dict[str, Any] = {}
    user_refs: dict[str, list[uuid.UUID]] = {}
    file_refs: dict[str, list[uuid.UUID]] = {}

    for key, value in payload.items():
        definition = by_key.get(key)
        if definition is None:
            problems.add(key, "isn't a known field")
            continue
        if value == existing.get(key) and key in existing:
            cleaned[key] = value  # unchanged: never re-judged (archived or not)
            continue
        if definition.archived_at is not None:
            problems.add(key, "is archived and can't be changed")
            continue
        if definition.is_sensitive and not can_edit_sensitive:
            problems.add(key, "can only be changed by someone with access to sensitive fields")
            continue
        if value is None:
            if definition.required:
                problems.add(key, "is required")
            continue
        if definition.field_type in ("user", "file"):
            refs = _as_uuid(value)
            if refs is None:
                problems.add(key, "must be an id")
                continue
            (user_refs if definition.field_type == "user" else file_refs).setdefault(
                key, []
            ).append(refs)
            cleaned[key] = str(refs)
            continue
        normalised, error = _check_scalar(definition, value)
        if error:
            problems.add(key, error)
        else:
            cleaned[key] = normalised

    for definition in definitions:
        if (
            definition.is_sensitive
            and not can_edit_sensitive
            and definition.key in existing
            and definition.key not in payload
        ):
            cleaned[definition.key] = existing[definition.key]  # keep what they couldn't see
        if (
            definition.archived_at is None
            and definition.required
            and cleaned.get(definition.key) in (None, "", [])
        ):
            problems.add(definition.key, "is required")

    known_users = await _active_user_ids(db, [i for ids in user_refs.values() for i in ids])
    for key, ids in user_refs.items():
        if not set(ids) <= known_users:
            problems.add(key, "isn't an active member of this organisation")
    usable_files = await _usable_file_ids(db, [i for ids in file_refs.values() for i in ids])
    for key, ids in file_refs.items():
        if not set(ids) <= usable_files:
            problems.add(key, "isn't an available file")

    if problems.errors:
        raise UnprocessableError(
            "Some custom fields are invalid.",
            code="invalid_custom_fields",
            details={"fields": problems.errors},
        )
    return cleaned


async def visible_custom_fields(
    db: AsyncSession, entity_type: str, payload: dict[str, Any], *, can_read_sensitive: bool
) -> dict[str, Any]:
    """Drop sensitive fields from ``payload`` unless the caller may see them."""
    if can_read_sensitive:
        return payload
    sensitive = {
        d.key
        for d in await list_definitions(db, entity_type, include_archived=True)
        if d.is_sensitive
    }
    return {k: v for k, v in payload.items() if k not in sensitive}
