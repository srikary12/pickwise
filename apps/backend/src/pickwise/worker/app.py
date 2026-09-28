# SPDX-License-Identifier: AGPL-3.0-only
"""procrastinate application (composition root for background jobs).

"procrastinate" is the name of the open-source, Postgres-backed job-queue library
(https://procrastinate.readthedocs.io). It runs our background jobs; it has
nothing to do with monitoring people.

procrastinate's objects live in the ``queue`` schema (Alembic revision 0000); its
SQL is unqualified, so every connector pins ``search_path`` to ``queue``.
The worker connects to Postgres directly, never through PgBouncer: procrastinate
needs LISTEN/NOTIFY and session-level advisory locks.

Job arguments carry only ids and tenant_id (CLAUDE.md rule 14): the queue tables
are global, so anything in them is visible across tenants. A test inspects every
registered task.
"""

import procrastinate

from pickwise.shared.settings import Settings, get_settings

QUEUE_SCHEMA = "queue"


def build_connector(settings: Settings) -> procrastinate.PsycopgConnector:
    return procrastinate.PsycopgConnector(
        conninfo=settings.psycopg_conninfo(),
        kwargs={
            "options": f"-c search_path={QUEUE_SCHEMA}",
            "application_name": settings.database_user,
        },
        min_size=1,
        max_size=4,
    )


app = procrastinate.App(
    connector=build_connector(get_settings()),
    import_paths=["pickwise.worker.tasks"],
)
