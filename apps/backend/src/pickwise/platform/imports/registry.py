# SPDX-License-Identifier: AGPL-3.0-only
"""Import types: what a module can bulk-load, and how.

A module registers a type (core: ``employees``; leave: ``leave_balances``; …) with its
columns, a row validator and a committer. Platform owns everything else: file handling,
parsing, the dry run, the error file, batching, progress and audit.

- ``validate_row`` runs in a tenant session, for the dry run and again just before each
  batch is committed. It returns every problem with the row; it must not write.
- ``commit_rows`` runs in a tenant session per batch and writes the rows. It must be
  idempotent (natural keys, ``ON CONFLICT``): a failed import can be committed again and
  the batches already written are written again harmlessly.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ImportColumn:
    name: str  # lower snake_case; the file's header is matched to it after normalising
    label: str
    required: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class RowError:
    message: str  # never includes personal data from the row
    column: str | None = None


@dataclass(frozen=True, slots=True)
class ImportRow:
    line: int  # the row number in the spreadsheet (header is row 1)
    values: dict[str, str]


RowValidator = Callable[[AsyncSession, ImportRow], Awaitable[list[RowError]]]
RowCommitter = Callable[[AsyncSession, uuid.UUID, list[ImportRow]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ImportType:
    key: str
    label: str
    columns: tuple[ImportColumn, ...]
    validate_row: RowValidator
    commit_rows: RowCommitter
    # The permission needed to import this type, in addition to platform.imports.run.
    permission: str = "platform.imports.run"
    # Declared by the module so the UI can show an example row.
    description: str = ""
    sample: dict[str, str] = field(default_factory=dict)


class ImportTypeRegistry:
    def __init__(self) -> None:
        self._types: dict[str, ImportType] = {}

    def register(self, import_type: ImportType) -> None:
        existing = self._types.get(import_type.key)
        if existing is not None and existing != import_type:
            raise ValueError(f"import type {import_type.key} registered twice")
        names = [c.name for c in import_type.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"import type {import_type.key} has duplicate columns")
        self._types[import_type.key] = import_type

    def unregister(self, key: str) -> None:
        self._types.pop(key, None)

    def get(self, key: str) -> ImportType | None:
        return self._types.get(key)

    def all(self) -> list[ImportType]:
        return sorted(self._types.values(), key=lambda t: t.key)


IMPORT_TYPES = ImportTypeRegistry()
