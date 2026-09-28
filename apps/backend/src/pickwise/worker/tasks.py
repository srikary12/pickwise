# SPDX-License-Identifier: AGPL-3.0-only
"""Task registry. Phase 0 has a single no-op periodic task that proves the wiring."""

from pickwise.shared.logging import get_logger
from pickwise.worker.app import app

log = get_logger(__name__)


@app.periodic(cron="*/5 * * * *", periodic_id="heartbeat")
@app.task(name="pickwise.heartbeat", queue="default", queueing_lock="heartbeat")
async def heartbeat(timestamp: int) -> None:
    """No-op: shows the periodic deferrer and a worker are both alive."""
    log.debug("heartbeat", scheduled_at=timestamp)
