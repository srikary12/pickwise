# SPDX-License-Identifier: AGPL-3.0-only
"""Database engine factory.

Phase 0 provides only the engine. ``tenant_session()`` and ``ops_session()``
(CLAUDE.md rules 3 and 4) arrive in Phase 1; until then nothing in the app
reads tenant data.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from pickwise.shared.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.sqlalchemy_url(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"server_settings": {"application_name": settings.database_user}},
    )
