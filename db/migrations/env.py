# SPDX-License-Identifier: AGPL-3.0-only
"""Alembic environment.

Migrations always run as ``pickwise_migrator`` with ``SET ROLE pickwise_owner`` so
every object is owned by the owner role (CLAUDE.md rule 4). They use psycopg
(sync); the application itself uses asyncpg.
"""

import os

import psycopg
from alembic import context
from sqlalchemy import create_engine, pool, text

VERSION_TABLE_SCHEMA = "public"


def _url() -> str:
    conninfo = psycopg.conninfo.make_conninfo(
        host=os.environ.get("DATABASE_HOST", "postgres"),
        port=os.environ.get("DATABASE_PORT", "5432"),
        dbname=os.environ.get("DATABASE_NAME", "pickwise"),
        user="pickwise_migrator",
        password=os.environ["PG_MIGRATOR_PASSWORD"],
        application_name="pickwise-alembic",
    )
    return "postgresql+psycopg:///?" + "&".join(
        f"{k}={v}" for k, v in psycopg.conninfo.conninfo_to_dict(conninfo).items()
    )


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text("SET ROLE pickwise_owner"))
        # Fail fast instead of queueing behind a long lock on a live database.
        connection.execute(text("SET lock_timeout = '5s'"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table_schema=VERSION_TABLE_SCHEMA,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline (--sql) migrations are not supported; migrations are hand-reviewed")
run_migrations_online()
