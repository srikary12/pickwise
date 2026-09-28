# SPDX-License-Identifier: AGPL-3.0-only
"""Refuse to start in configurations that are unsafe for production.

Both the API and the worker run these before serving traffic.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from pickwise.shared.settings import ScannerKind, Settings


class UnsafeConfigurationError(RuntimeError):
    """Raised when the process must not start with the current configuration."""


def check_settings(settings: Settings) -> None:
    if settings.is_production and settings.scanner is ScannerKind.STUB:
        raise UnsafeConfigurationError(
            "SCANNER=stub is not allowed when PICKWISE_ENV=production: "
            "set SCANNER=clamav and run clamd"
        )


async def check_database(settings: Settings, engine: AsyncEngine) -> None:
    """Production must never contain test_fixture statutory rule sets (CLAUDE.md rule 27).

    The table arrives in Phase 8; until then the check is a no-op.
    """
    if not settings.is_production:
        return
    async with engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT to_regclass('payroll.statutory_rule_sets') IS NOT NULL")
        )
        if not exists:
            return
        fixtures = await conn.scalar(
            text("SELECT count(*) FROM payroll.statutory_rule_sets WHERE status = 'test_fixture'")
        )
    if fixtures:
        raise UnsafeConfigurationError(
            f"{fixtures} test_fixture statutory rule set(s) found in a production database"
        )


async def run_startup_checks(settings: Settings, engine: AsyncEngine) -> None:
    check_settings(settings)
    await check_database(settings, engine)
