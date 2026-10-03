# SPDX-License-Identifier: AGPL-3.0-only
"""Transactional email through an outbox (ADR 0012).

- Tenant email (invites) goes to ``platform.email_outbox`` (RLS); pre-tenant email
  (verification, password reset, signup) to the global ``platform.platform_email_outbox``.
- ``payload`` holds non-secret template variables. Secrets such as link tokens go
  in ``payload_enc``, encrypted with the tenant (or platform) data key and nulled
  once the message is sent.
- Queueing happens inside the caller's transaction. Sending happens in the worker
  (an ``@ops_task``), triggered right after commit and by a periodic sweep.
"""

import asyncio
import json
import smtplib
import ssl
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.crypto import (
    KeyEncryptionKey,
    Keyring,
    load_platform_keyring,
    load_tenant_keyring,
)
from pickwise.shared.logging import get_logger
from pickwise.shared.settings import Settings

log = get_logger(__name__)

MAX_ATTEMPTS = 5
TEMPLATES = frozenset({"invite", "verify_email", "reset_password", "signup_verify"})

_jinja = Environment(
    loader=PackageLoader("pickwise.platform.notifications", "templates"),
    autoescape=select_autoescape(enabled_extensions=("html.j2",), default_for_string=False),
    undefined=StrictUndefined,
    keep_trailing_newline=False,
)


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


@dataclass(frozen=True, slots=True)
class QueuedEmail:
    outbox_id: uuid.UUID
    tenant_id: uuid.UUID | None


def render(template_key: str, variables: dict[str, Any]) -> RenderedEmail:
    if template_key not in TEMPLATES:
        raise ValueError(f"unknown email template {template_key!r}")
    variables = {"locale": "en-IN", **variables}
    return RenderedEmail(
        subject=_jinja.get_template(f"{template_key}.subject.j2").render(**variables).strip(),
        text=_jinja.get_template(f"{template_key}.txt.j2").render(**variables).strip() + "\n",
        html=_jinja.get_template(f"{template_key}.html.j2").render(**variables),
    )


def _aad(table: str, outbox_id: uuid.UUID) -> bytes:
    return f"{table}.payload_enc:{outbox_id}".encode()


_TENANT_INSERT = text(
    "INSERT INTO platform.email_outbox "
    "(id, to_address, template_key, locale, payload, payload_enc) "
    "VALUES (:id, :to, :template, :locale, :payload, :enc)"
).bindparams(bindparam("payload", type_=JSONB))
_PLATFORM_INSERT = text(
    "INSERT INTO platform.platform_email_outbox "
    "(id, to_address, template_key, locale, payload, payload_enc) "
    "VALUES (:id, :to, :template, :locale, :payload, :enc)"
).bindparams(bindparam("payload", type_=JSONB))


async def queue_email(
    session: AsyncSession,
    keyring: Keyring,
    *,
    tenant_id: uuid.UUID | None,
    to_address: str,
    template_key: str,
    variables: dict[str, Any],
    secrets: dict[str, str],
    locale: str = "en-IN",
) -> QueuedEmail:
    """Queue one email in the current transaction.

    ``tenant_id`` None means the pre-tenant outbox, and ``keyring`` must then be the
    platform keyring; otherwise it's that tenant's keyring. The row is written once
    (the app role can't UPDATE the global outbox), so the id is chosen first: it's
    part of the ciphertext's AAD.
    """
    render(template_key, {**variables, **dict.fromkeys(secrets, "")})  # fail fast on bad templates
    table = "platform.email_outbox" if tenant_id else "platform.platform_email_outbox"
    insert = _TENANT_INSERT if tenant_id else _PLATFORM_INSERT
    outbox_id = uuid.UUID(str((await session.execute(text("SELECT uuidv7()"))).scalar_one()))
    await session.execute(
        insert,
        {
            "id": outbox_id,
            "to": to_address,
            "template": template_key,
            "locale": locale,
            "payload": variables,
            "enc": keyring.encrypt(json.dumps(secrets).encode(), _aad(table, outbox_id)),
        },
    )
    return QueuedEmail(outbox_id, tenant_id)


# The composition root (API, worker) installs a function that defers the send job
# right after commit; without one, the periodic sweep sends within a minute.
EmailDispatcher = Callable[[QueuedEmail], Awaitable[None]]
_dispatcher: EmailDispatcher | None = None


def set_dispatcher(dispatcher: EmailDispatcher | None) -> None:
    global _dispatcher
    _dispatcher = dispatcher


async def dispatch(emails: list[QueuedEmail]) -> None:
    """Call after the transaction that queued ``emails`` has committed."""
    if _dispatcher is None:
        return
    for email in emails:
        try:
            await _dispatcher(email)
        except Exception as exc:  # noqa: BLE001 - the sweep retries; never fail the request
            log.warning("email dispatch deferred to sweep", error=type(exc).__name__)


def _smtp_send(settings: Settings, to_address: str, rendered: RenderedEmail) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_address
    message["Subject"] = rendered.subject
    message["Message-ID"] = make_msgid(domain="pickwise")
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)


async def send_queued(
    session: AsyncSession,
    settings: Settings,
    kek: KeyEncryptionKey,
    outbox_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
) -> str:
    """Send one queued email. Runs in an ops session. Returns the resulting status."""
    table = "platform.email_outbox" if tenant_id else "platform.platform_email_outbox"
    row = (
        await session.execute(
            text(
                f"SELECT to_address, template_key, payload, payload_enc, status, attempts "  # noqa: S608
                f"FROM {table} WHERE id = :id FOR UPDATE SKIP LOCKED"
            ),
            {"id": outbox_id},
        )
    ).one_or_none()
    if row is None or row.status != "queued":
        return "skipped"
    keyring = (
        await load_tenant_keyring(session, kek, tenant_id)
        if tenant_id
        else await load_platform_keyring(session, kek)
    )
    secrets: dict[str, str] = (
        json.loads(keyring.decrypt(bytes(row.payload_enc), _aad(table, outbox_id)))
        if row.payload_enc
        else {}
    )
    try:
        rendered = render(row.template_key, {**row.payload, **secrets})
        await asyncio.to_thread(_smtp_send, settings, str(row.to_address), rendered)
    except Exception as exc:  # noqa: BLE001 - recorded, retried by the sweep
        attempts = row.attempts + 1
        status = "failed" if attempts >= MAX_ATTEMPTS else "queued"
        await session.execute(
            text(
                f"UPDATE {table} SET attempts = :a, status = :s, last_error = :e, "  # noqa: S608
                f"payload_enc = CASE WHEN :s = 'failed' THEN NULL ELSE payload_enc END "
                f"WHERE id = :id"
            ),
            {"a": attempts, "s": status, "e": type(exc).__name__, "id": outbox_id},
        )
        log.warning("email send failed", outbox_id=str(outbox_id), attempts=attempts, status=status)
        return status
    await session.execute(
        text(
            f"UPDATE {table} SET status = 'sent', sent_at = now(), attempts = attempts + 1, "  # noqa: S608
            f"payload_enc = NULL, last_error = NULL WHERE id = :id"
        ),
        {"id": outbox_id},
    )
    log.info("email sent", outbox_id=str(outbox_id), template=row.template_key)
    return "sent"


async def due_emails(session: AsyncSession, limit: int = 50) -> list[QueuedEmail]:
    """Queued emails (both outboxes) waiting longer than a minute. Ops session."""
    rows = (
        await session.execute(
            text(
                "(SELECT id, tenant_id FROM platform.email_outbox WHERE status = 'queued' "
                " AND created_at < now() - interval '1 minute' ORDER BY created_at LIMIT :n) "
                "UNION ALL "
                "(SELECT id, NULL::uuid FROM platform.platform_email_outbox "
                " WHERE status = 'queued' "
                " AND created_at < now() - interval '1 minute' ORDER BY created_at LIMIT :n)"
            ),
            {"n": limit},
        )
    ).all()
    return [QueuedEmail(r[0], r[1]) for r in rows]
