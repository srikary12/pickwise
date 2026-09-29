# SPDX-License-Identifier: AGPL-3.0-only
"""Task registry. Phase 0 has a single no-op periodic task that proves the wiring."""

from pickwise.platform.partitions import ensure_partitions
from pickwise.shared.db import ops_task
from pickwise.shared.logging import get_logger
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
