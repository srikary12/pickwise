# SPDX-License-Identifier: AGPL-3.0-only
"""Alembic revision 0000 vendors procrastinate's schema; it must match the pinned library."""

from importlib.metadata import version
from pathlib import Path

from procrastinate.schema import SchemaManager

VENDORED = Path(__file__).resolve().parents[4] / "db" / "migrations" / "vendor"


def test_vendored_schema_matches_installed_procrastinate() -> None:
    installed = version("procrastinate")
    vendored = VENDORED / f"procrastinate-{installed}-schema.sql"
    assert vendored.exists(), (
        f"procrastinate {installed} is installed but {vendored.name} isn't vendored: "
        "upgrading procrastinate needs a new Alembic revision wrapping its migration SQL"
    )
    assert vendored.read_text(encoding="utf-8") == SchemaManager.get_schema()
