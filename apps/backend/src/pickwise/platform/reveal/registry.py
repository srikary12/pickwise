# SPDX-License-Identifier: AGPL-3.0-only
"""Which masked values can be revealed, by whom, and how they are decrypted.

A module that stores a restricted value (core: PAN, bank account; payroll: UAN) registers a
field here. The platform checks the field's permission, writes the ``pii.reveal`` audit event
and returns the value once; the module only says how to read it for one record (and applies
the caller's data scope, raising NotFoundError for a record they may not see).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.dependencies import Authorized

RevealFunction = Callable[[AsyncSession, Authorized, uuid.UUID], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class RevealField:
    entity_type: str
    field: str
    #: 'schema.table' written on the audit event
    entity: str
    permission: str
    reveal: RevealFunction


class RevealRegistry:
    def __init__(self) -> None:
        self._fields: dict[tuple[str, str], RevealField] = {}

    def register(self, field: RevealField) -> None:
        key = (field.entity_type, field.field)
        existing = self._fields.get(key)
        if existing is not None and existing != field:
            raise ValueError(f"reveal field {key} registered twice")
        self._fields[key] = field

    def unregister(self, entity_type: str, field: str) -> None:
        self._fields.pop((entity_type, field), None)

    def get(self, entity_type: str, field: str) -> RevealField | None:
        return self._fields.get((entity_type, field))


REVEAL_FIELDS = RevealRegistry()
