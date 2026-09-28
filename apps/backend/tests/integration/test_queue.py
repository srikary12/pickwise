# SPDX-License-Identifier: AGPL-3.0-only
"""procrastinate (the job-queue library) works from the `queue` schema for app roles."""

import os
import time

import procrastinate
import psycopg
import pytest
from pydantic import SecretStr

from pickwise.shared.settings import Settings
from pickwise.worker.app import build_connector

pytestmark = pytest.mark.db


@pytest.mark.parametrize(
    ("user", "password_env"),
    [("pickwise_api", "PG_API_PASSWORD"), ("pickwise_worker", "PG_WORKER_PASSWORD")],
)
async def test_app_roles_can_defer_jobs(user: str, password_env: str) -> None:
    settings = Settings(database_user=user, database_password=SecretStr(os.environ[password_env]))
    app = procrastinate.App(connector=build_connector(settings))

    @app.task(name="pickwise.test.noop")
    async def noop(marker: int) -> None:
        return None

    marker = time.time_ns()
    async with app.open_async():
        job_id = await noop.defer_async(marker=marker)

    with psycopg.connect(settings.psycopg_conninfo()) as conn:
        row = conn.execute(
            "SELECT task_name, args FROM queue.procrastinate_jobs WHERE id = %s", (job_id,)
        ).fetchone()
        conn.execute("DELETE FROM queue.procrastinate_jobs WHERE id = %s", (job_id,))
    assert row == ("pickwise.test.noop", {"marker": marker})
