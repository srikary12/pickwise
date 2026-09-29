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
    if not settings.is_production:
        return
    if len(settings.session_secret.get_secret_value()) < 32:
        raise UnsafeConfigurationError(
            "SESSION_SECRET must be at least 32 characters in production"
        )
    if not settings.pickwise_kek.get_secret_value():
        raise UnsafeConfigurationError("PICKWISE_KEK must be set in production")
    if not settings.secure_cookies:
        raise UnsafeConfigurationError("PUBLIC_BASE_URL must be https:// in production")
    if settings.demo_password.get_secret_value():
        raise UnsafeConfigurationError("DEMO_PASSWORD must not be set in production")


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
