# SPDX-License-Identifier: AGPL-3.0-only
"""Running an erasure.

``run_erasure`` takes an ops session (the caller is an ``@ops_task`` or ``@ops_command``),
enters purge mode through ``platform.begin_erasure()``, runs every registered handler, scrubs
the audit rows they reported, leaves purge mode and writes one semantic audit event with
counts. If a data-subject request id is given, that request is marked completed in the same
transaction.

Phase 3 provides the mechanism and the platform handler. Each later module registers its
own handler with its own tables; the data-subject-request workflow (intake, identity
checks, deadlines) arrives with the privacy work in Phase 12.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit
from pickwise.platform.erasure.registry import (
    ERASURE_HANDLERS,
    ErasureContext,
    ErasureHandlerRegistry,
    ErasureSubject,
)
from pickwise.shared.errors import NotFoundError
from pickwise.shared.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class ErasureReport:
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    audit_rows_scrubbed: int = 0


async def run_erasure(
    session: AsyncSession,
    s3: object,
    subject: ErasureSubject,
    *,
    request_id: uuid.UUID | None = None,
    registry: ErasureHandlerRegistry = ERASURE_HANDLERS,
) -> ErasureReport:
    """Erase ``subject`` across every module. ``session`` must be an ops session."""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(subject.tenant_id)}
    )
    if request_id is not None:
        found = (
            await session.execute(
                text(
                    "SELECT 1 FROM platform.data_subject_requests "
                    "WHERE tenant_id = :t AND id = :id AND request_type = 'erasure' "
                    "  AND subject_type = :st AND subject_id = :sid"
                ),
                {
                    "t": subject.tenant_id,
                    "id": request_id,
                    "st": subject.subject_type,
                    "sid": subject.subject_id,
                },
            )
        ).first()
        if found is None:
            raise NotFoundError("No erasure request matches that subject.")
    report = ErasureReport()
    await session.execute(text("SELECT platform.begin_erasure()"))
    ctx = ErasureContext(session, subject, s3)
    try:
        for name, handler in registry.handlers():
            report.counts[name] = await handler(ctx)
        for target in dict.fromkeys(ctx.scrub_targets):
            scrubbed: int = (
                await session.execute(
                    text("SELECT audit.scrub_subject(:table, :id)"),
                    {"table": target.entity_table, "id": target.entity_id},
                )
            ).scalar_one()
            report.audit_rows_scrubbed += scrubbed
    finally:
        # Leave purge mode even when a handler failed: the caller's rollback would clear it
        # too, but a caller that catches the error and carries on must not stay in it.
        await session.execute(text("SELECT platform.end_erasure()"))
    if request_id is not None:
        await session.execute(
            text(
                "UPDATE platform.data_subject_requests SET status = 'completed', "
                "completed_at = now() WHERE tenant_id = :t AND id = :id"
            ),
            {"t": subject.tenant_id, "id": request_id},
        )
    await audit.record(
        session,
        "erasure.completed",
        "platform.data_subject_requests" if request_id else None,
        request_id,
        {
            "subject_type": subject.subject_type,
            "handlers": {name: sum(c.values()) for name, c in report.counts.items()},
            "audit_rows_scrubbed": report.audit_rows_scrubbed,
        },
    )
    log.info(
        "erasure completed",
        tenant_id=str(subject.tenant_id),
        subject_type=subject.subject_type,
        handlers=len(report.counts),
        audit_rows_scrubbed=report.audit_rows_scrubbed,
    )
    return report
