# SPDX-License-Identifier: AGPL-3.0-only
"""Worker entrypoint: ``python -m pickwise.worker``."""

import asyncio

from pickwise import wiring
from pickwise.platform.notifications.email import QueuedEmail, set_dispatcher
from pickwise.platform.scanning.stub import STUB_WARNING
from pickwise.platform.startup_checks import run_startup_checks
from pickwise.shared.db import create_engine
from pickwise.shared.logging import configure_logging, get_logger
from pickwise.shared.settings import ScannerKind, get_settings
from pickwise.worker.app import app

log = get_logger("pickwise.worker")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    try:
        await run_startup_checks(settings, engine)
    finally:
        await engine.dispose()
    if settings.scanner is ScannerKind.STUB:
        log.warning(STUB_WARNING)
    wiring.register_all()
    log.info("worker starting", environment=settings.pickwise_env.value)
    async with app.open_async():
        from pickwise.worker.tasks import send_email

        async def defer(email: QueuedEmail) -> None:
            await send_email.defer_async(
                outbox_id=str(email.outbox_id),
                tenant_id=str(email.tenant_id) if email.tenant_id else None,
            )

        set_dispatcher(defer)
        await app.run_worker_async(install_signal_handlers=True)


if __name__ == "__main__":
    asyncio.run(main())
