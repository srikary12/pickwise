# SPDX-License-Identifier: AGPL-3.0-only
"""Task registry. Arguments are ids only (CLAUDE.md rule 14; a test checks)."""

import datetime
import uuid

import httpx

from pickwise.core import maintenance as core_maintenance
from pickwise.platform import ratelimit, storage
from pickwise.platform.approvals import deadlines
from pickwise.platform.crypto import kek_from_settings
from pickwise.platform.events.relay import relay_batch
from pickwise.platform.files import processing
from pickwise.platform.imports import processing as import_processing
from pickwise.platform.jobs import defer
from pickwise.platform.notifications.email import dispatch, due_emails, send_queued
from pickwise.platform.partitions import ensure_partitions
from pickwise.platform.provisioning import signup
from pickwise.platform.scanning import ScannerError, build_scanner
from pickwise.platform.webhooks import delivery
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


@app.task(name="pickwise.scan_file", queue="default", retry=5)
@ops_task
async def scan_file(file_id: str, tenant_id: str) -> None:
    """Scan one uploaded file and move it to the clean bucket (deferred by /complete)."""
    settings = get_settings()
    scanner = build_scanner(settings)
    async with storage.s3_client(settings) as s3:
        try:
            async with get_database().ops_session() as session:
                outcome = await processing.scan_file(
                    session, settings, scanner, s3, uuid.UUID(tenant_id), uuid.UUID(file_id)
                )
        except ScannerError:
            # No verdict: raise so procrastinate retries; the sweep fails it after an hour.
            log.warning("scanner unavailable", file_id=file_id)
            raise
        if outcome.cleanup is not None:
            # Only after the commit above: a crash earlier just repeats the scan.
            await storage.delete_object(s3, *outcome.cleanup)
    log.info("file scanned", file_id=file_id, status=outcome.status)


@app.periodic(cron="* * * * *", periodic_id="sweep_files")
@app.task(name="pickwise.sweep_files", queue="default", queueing_lock="sweep_files")
@ops_task
async def sweep_files(timestamp: int) -> None:
    """Every minute: re-queue scans that never started, fail ones stuck for an hour, and
    close upload slots nobody completed."""
    settings = get_settings()
    async with get_database().ops_session() as session:
        pending, stuck = await processing.sweep(session)
    async with storage.s3_client(settings) as s3, get_database().ops_session() as session:
        expired = await processing.expire_unfinished(session, settings, s3)
    for tenant_id, file_id in pending:
        await defer("pickwise.scan_file", file_id=str(file_id), tenant_id=str(tenant_id))
    if pending or stuck or expired:
        log.info("files swept", requeued=len(pending), failed=stuck, expired=expired)


@app.periodic(cron="23 3 * * *", periodic_id="purge_expired_files")
@app.task(name="pickwise.purge_expired_files", queue="default", queueing_lock="purge_expired_files")
@ops_task
async def purge_expired_files(timestamp: int) -> None:
    """Daily: delete the bytes of files past their retention date."""
    settings = get_settings()
    async with storage.s3_client(settings) as s3, get_database().ops_session() as session:
        removed = await processing.purge_retained(session, s3)
    log.info("retained files purged", removed=removed)


@ops_task
async def _relay_events() -> None:
    async with get_database().ops_session() as session:
        result = await relay_batch(session, get_database())
    for tenant_id, delivery_id in result.deliveries:
        await defer(
            "pickwise.deliver_webhook", delivery_id=str(delivery_id), tenant_id=str(tenant_id)
        )
    if result.published or result.failed:
        log.info(
            "events relayed",
            published=result.published,
            failed=result.failed,
            deliveries=len(result.deliveries),
        )


@app.task(name="pickwise.relay_events", queue="default", queueing_lock="relay_events")
@ops_task
async def relay_events() -> None:
    """Publish committed outbox events (deferred right after a commit that emitted one)."""
    await _relay_events()


@app.periodic(cron="* * * * *", periodic_id="sweep_events")
@app.task(name="pickwise.sweep_events", queue="default", queueing_lock="sweep_events")
@ops_task
async def sweep_events(timestamp: int) -> None:
    """Every minute: publish events whose immediate relay didn't happen, and retry failures."""
    await _relay_events()


@app.task(name="pickwise.deliver_webhook", queue="default", retry=False)
@ops_task
async def deliver_webhook(delivery_id: str, tenant_id: str) -> None:
    """Attempt one webhook delivery (a no-op unless it is due and still pending)."""
    settings = get_settings()
    async with httpx.AsyncClient(follow_redirects=False) as client:
        await delivery.deliver_one(
            get_database(),
            settings,
            kek_from_settings(settings),
            client,
            uuid.UUID(tenant_id),
            uuid.UUID(delivery_id),
        )


@app.periodic(cron="* * * * *", periodic_id="sweep_webhooks")
@app.task(name="pickwise.sweep_webhooks", queue="default", queueing_lock="sweep_webhooks")
@ops_task
async def sweep_webhooks(timestamp: int) -> None:
    """Every minute: queue deliveries whose time has come (retries, missed kicks, leases)."""
    now = datetime.datetime.now(datetime.UTC)
    async with get_database().ops_session() as session:
        due = await delivery.due_deliveries(session, now=now)
    for tenant_id, delivery_id in due:
        await defer(
            "pickwise.deliver_webhook", delivery_id=str(delivery_id), tenant_id=str(tenant_id)
        )
    if due:
        log.info("webhook deliveries queued", count=len(due))


@app.periodic(cron="* * * * *", periodic_id="escalate_approvals")
@app.task(name="pickwise.escalate_approvals", queue="default", queueing_lock="escalate_approvals")
@ops_task
async def escalate_approvals(timestamp: int) -> None:
    """Every minute: remind about approval tasks half way to their deadline and hand
    overdue ones to the fallback approver."""
    emails = await deadlines.run_deadlines(get_database(), kek_from_settings(get_settings()))
    await dispatch(emails)
    if emails:
        log.info("approval deadlines processed", emails=len(emails))


@app.task(name="pickwise.validate_import", queue="default", retry=False)
@ops_task
async def validate_import(import_id: str, tenant_id: str) -> None:
    """The dry run of an import (deferred right after the import is created)."""
    settings = get_settings()
    async with storage.s3_client(settings) as s3:
        outcome = await import_processing.run_validation(
            get_database(), settings, s3, uuid.UUID(tenant_id), uuid.UUID(import_id)
        )
    log.info("import validated", import_id=import_id, outcome=outcome)


@app.task(name="pickwise.commit_import", queue="default", retry=False)
@ops_task
async def commit_import(import_id: str, tenant_id: str) -> None:
    """Write a validated import in batches (deferred when the commit is requested)."""
    settings = get_settings()
    async with storage.s3_client(settings) as s3:
        outcome = await import_processing.run_commit(
            get_database(), settings, s3, uuid.UUID(tenant_id), uuid.UUID(import_id)
        )
    log.info("import committed", import_id=import_id, outcome=outcome)


@app.periodic(cron="* * * * *", periodic_id="sweep_imports")
@app.task(name="pickwise.sweep_imports", queue="default", queueing_lock="sweep_imports")
@ops_task
async def sweep_imports(timestamp: int) -> None:
    """Every minute: re-queue dry runs whose job was lost, fail imports stuck mid-run."""
    async with get_database().ops_session() as session:
        pending, stuck = await import_processing.sweep(session)
    for tenant_id, import_id in pending:
        await defer("pickwise.validate_import", import_id=str(import_id), tenant_id=str(tenant_id))
    if pending or stuck:
        log.info("imports swept", requeued=len(pending), failed=stuck)


@app.periodic(cron="7 1 * * *", periodic_id="refresh_hierarchy")
@app.task(name="pickwise.refresh_hierarchy", queue="default", queueing_lock="refresh_hierarchy")
@ops_task
async def refresh_hierarchy(timestamp: int) -> None:
    """Daily, just after midnight IST: rebuild every tenant's reporting tree so manager changes
    scheduled for today take effect, and heal any drift (ADR 0022)."""
    async with get_database().ops_session() as session:
        tenants = await core_maintenance.active_tenant_ids(session)
    for tenant_id in tenants:
        await core_maintenance.rebuild_tenant_hierarchy(get_database(), tenant_id)
    log.info("reporting hierarchy refreshed", tenants=len(tenants))
