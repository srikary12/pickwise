# SPDX-License-Identifier: AGPL-3.0-only
"""Platform's erasure handler: files, consent evidence, notifications and mail.

Registered by ``wiring.register_all``. What it does and why:

- **Files** owned by the subject lose their bytes (both buckets) and their name and hash; the
  row stays, marked purged, so references don't dangle.
- **Consents** keep the fact and time of consent, which the tenant must be able to show,
  and lose ``evidence`` (IP address, user agent).
- **Notifications** about or to the subject are deleted, and so is **mail queued to their
  address** (including mail already sent: the outbox row holds the address).
- **Data-subject requests** about the subject keep their record and deadlines but lose
  free-text ``notes``.

The login itself (``platform.users``, ``memberships``) is not touched here: a user is global
and may belong to other tenants, so closing it is a separate decision made by core's handler
(Phase 12).
"""

from sqlalchemy import text

from pickwise.platform import storage
from pickwise.platform.erasure.registry import ERASURE_HANDLERS, ErasureContext


async def erase_platform_data(ctx: ErasureContext) -> dict[str, int]:
    s, session = ctx.subject, ctx.session
    params = {"t": s.tenant_id, "st": s.subject_type, "sid": s.subject_id}

    files = (
        await session.execute(
            text(
                "UPDATE platform.files SET purged_at = coalesce(purged_at, now()), "
                "  original_name = '[erased]', sha256 = NULL, scan_detail = NULL "
                "WHERE tenant_id = :t AND owner_entity_type = :st AND owner_entity_id = :sid "
                "  AND (purged_at IS NULL OR original_name <> '[erased]') "
                "RETURNING id, bucket, storage_key"
            ),
            params,
        )
    ).all()
    for file_id, bucket, key in files:
        await storage.delete_object(ctx.s3, bucket, key)
        ctx.scrub("platform.files", file_id)

    consents = (
        await session.execute(
            text(
                "UPDATE platform.consents SET evidence = '{}' "
                "WHERE tenant_id = :t AND subject_type = :st AND subject_id = :sid "
                "  AND evidence <> '{}' RETURNING id"
            ),
            params,
        )
    ).all()
    for (consent_id,) in consents:
        ctx.scrub("platform.consents", consent_id)

    notifications = (
        await session.execute(
            text(
                "DELETE FROM platform.notifications "
                "WHERE tenant_id = :t AND (user_id = CAST(:uid AS uuid) "
                "  OR (entity_type = :st AND entity_id = :sid)) RETURNING id"
            ),
            {**params, "uid": s.user_id},
        )
    ).all()
    for (notification_id,) in notifications:
        ctx.scrub("platform.notifications", notification_id)

    mail = 0
    if s.email:
        deleted = (
            await session.execute(
                text(
                    "DELETE FROM platform.email_outbox "
                    "WHERE tenant_id = :t AND to_address = :email RETURNING id"
                ),
                {"t": s.tenant_id, "email": s.email},
            )
        ).all()
        mail = len(deleted)
        for (outbox_id,) in deleted:
            ctx.scrub("platform.email_outbox", outbox_id)

    requests = (
        await session.execute(
            text(
                "UPDATE platform.data_subject_requests SET notes = NULL "
                "WHERE tenant_id = :t AND subject_type = :st AND subject_id = :sid "
                "  AND notes IS NOT NULL RETURNING id"
            ),
            params,
        )
    ).all()
    for (request_id,) in requests:
        ctx.scrub("platform.data_subject_requests", request_id)

    return {
        "files": len(files),
        "consents": len(consents),
        "notifications": len(notifications),
        "emails": mail,
        "data_subject_requests": len(requests),
    }


ERASURE_HANDLERS.register("platform", erase_platform_data, order=900)
