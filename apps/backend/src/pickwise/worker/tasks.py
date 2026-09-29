# SPDX-License-Identifier: AGPL-3.0-only
"""Task registry. Arguments are ids only (CLAUDE.md rule 14; a test checks)."""

import uuid

from pickwise.platform import ratelimit
from pickwise.platform.crypto import kek_from_settings
from pickwise.platform.notifications.email import dispatch, due_emails, send_queued
from pickwise.platform.partitions import ensure_partitions
from pickwise.platform.provisioning import signup
from pickwise.shared.db import ops_task
from pickwise.shared.logging import get_logger
from pickwise.shared.settings import get_settings
from pickwise.worker.app import app, get_database

log = get_logger(__name__)


@app.periodic(cron="*/5 * * * *", periodic_id="heartbeat")
@app.task(name="pickwise.heartbeat", queue="default", queueing_lock="heartbeat")
async def heartbeat(timestamp: int) -> None:
    """No-op: shows the periodic deferrer and a worker are both alive."""
    log.debug("heartbeat", scheduled_at=timestamp)


@app.periodic(cron="17 2 * * *", periodic_id="ensure_partitions")
@app.task(name="pickwise.ensure_partitions", queue="default", queueing_lock="ensure_partitions")
@ops_task
async def ensure_partitions_task(timestamp: int) -> None:
    """Daily: keep monthly partitions three months ahead (ADR 0003)."""
    async with get_database().ops_session() as session:
        created = await ensure_partitions(session)
    log.info("partitions ensured", created=sum(created.values()), tables=len(created))


@app.task(name="pickwise.send_email", queue="default", retry=False)
@ops_task
async def send_email(outbox_id: str, tenant_id: str | None = None) -> None:
    """Send one queued email (deferred right after the queuing transaction commits).

    Ids travel as strings: job arguments are JSON.
    """
    settings = get_settings()
    async with get_database().ops_session() as session:
        await send_queued(
            session,
            settings,
            kek_from_settings(settings),
            uuid.UUID(outbox_id),
            uuid.UUID(tenant_id) if tenant_id else None,
        )


@app.periodic(cron="* * * * *", periodic_id="sweep_emails")
@app.task(name="pickwise.sweep_emails", queue="default", queueing_lock="sweep_emails")
@ops_task
async def sweep_emails(timestamp: int) -> None:
    """Every minute: retry queued emails whose immediate send didn't happen or failed."""
    settings = get_settings()
    kek = kek_from_settings(settings)
    async with get_database().ops_session() as session:
        pending = await due_emails(session)
    for email in pending:
        async with get_database().ops_session() as session:
            await send_queued(session, settings, kek, email.outbox_id, email.tenant_id)


@app.task(name="pickwise.provision_signup", queue="default", retry=3)
@ops_task
async def provision_signup(signup_id: str) -> None:
    """Create the tenant for a verified self-serve signup (ADR 0007)."""
    settings = get_settings()
    async with get_database().ops_session() as session:
        invite = await signup.provision(
            session, settings, kek_from_settings(settings), uuid.UUID(signup_id)
        )
    if invite is not None:
        await dispatch([invite])


@app.periodic(cron="41 3 * * *", periodic_id="prune_rate_limits")
@app.task(name="pickwise.prune_rate_limits", queue="default", queueing_lock="prune_rate_limits")
@ops_task
async def prune_rate_limits(timestamp: int) -> None:
    """Daily: drop rate-limit buckets idle for a day (they'd be full again anyway)."""
    async with get_database().ops_session() as session:
        removed = await ratelimit.prune(session)
    log.info("rate-limit buckets pruned", removed=removed)
