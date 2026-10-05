# SPDX-License-Identifier: AGPL-3.0-only
"""A practice import type for development and test environments only.

Real import types arrive with the modules that own the data (core: employees, leave:
balances, …). Until then the import screens and the e2e tests need something to exercise,
so ``sandbox_contacts`` validates names and email addresses and writes nothing. It is never
registered in production.
"""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.imports.registry import (
    IMPORT_TYPES,
    ImportColumn,
    ImportRow,
    ImportType,
    RowError,
)
from pickwise.shared.settings import Environment, get_settings

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def validate_row(session: AsyncSession, row: ImportRow) -> list[RowError]:
    if not _EMAIL.match(row.values["email"]):
        return [RowError("isn't a valid email address", "email")]
    return []


async def commit_rows(session: AsyncSession, tenant_id: uuid.UUID, rows: list[ImportRow]) -> None:
    """Nothing to write: this type exists to practise the flow."""


if get_settings().pickwise_env in (Environment.DEVELOPMENT, Environment.TEST):
    IMPORT_TYPES.register(
        ImportType(
            "sandbox_contacts",
            "Practice import (writes nothing)",
            (
                ImportColumn("name", "Name", required=True),
                ImportColumn("email", "Email", required=True, description="A valid address"),
            ),
            validate_row,
            commit_rows,
            description="Try the import flow with a CSV or Excel file. Nothing is stored.",
            sample={"name": "Asha Rao", "email": "asha@example.com"},
        )
    )
