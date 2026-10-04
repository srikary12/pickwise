# SPDX-License-Identifier: AGPL-3.0-only
"""Who may download a file besides its uploader and holders of ``platform.files.read_all``.

A module that attaches files to its own entities (a resume to an application, a payslip
to an employee) registers a rule for its ``owner_entity_type``; platform never imports it.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.files.service import FileRecord
from pickwise.platform.rbac.principal import Principal

FileAccessRule = Callable[[AsyncSession, Principal, FileRecord], Awaitable[bool]]


class FileAccessRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, FileAccessRule] = {}

    def register(self, owner_entity_type: str, rule: FileAccessRule) -> None:
        self._rules[owner_entity_type] = rule

    def unregister(self, owner_entity_type: str) -> None:
        self._rules.pop(owner_entity_type, None)

    def rule(self, owner_entity_type: str | None) -> FileAccessRule | None:
        return self._rules.get(owner_entity_type) if owner_entity_type else None


FILE_ACCESS = FileAccessRegistry()
