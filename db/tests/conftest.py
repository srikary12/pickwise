# SPDX-License-Identifier: AGPL-3.0-only
"""SQL-level tests. They connect as the real login users of the test stack."""

import os
from collections.abc import Callable, Iterator

import psycopg
import pytest

PASSWORD_ENV = {
    "pickwise_migrator": "PG_MIGRATOR_PASSWORD",
    "pickwise_api": "PG_API_PASSWORD",
    "pickwise_worker": "PG_WORKER_PASSWORD",
    "pickwise_maint": "PG_MAINT_PASSWORD",
}

Connect = Callable[[str], psycopg.Connection[tuple[object, ...]]]


@pytest.fixture
def connect() -> Iterator[Connect]:
    opened: list[psycopg.Connection[tuple[object, ...]]] = []

    def _connect(user: str) -> psycopg.Connection[tuple[object, ...]]:
        conn = psycopg.connect(
            host=os.environ.get("DATABASE_HOST", "postgres"),
            port=int(os.environ.get("DATABASE_PORT", "5432")),
            dbname=os.environ.get("DATABASE_NAME", "pickwise"),
            user=user,
            password=os.environ[PASSWORD_ENV[user]],
            autocommit=True,
        )
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        conn.close()
