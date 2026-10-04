# SPDX-License-Identifier: AGPL-3.0-only
"""Helpers shared by the API integration tests: tenants, accounts, a mailbox, an HTTP wrapper."""

import datetime
import itertools
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pyotp
from fastapi import FastAPI
from sqlalchemy import text

from pickwise.platform import storage
from pickwise.platform.auth.passwords import hash_password
from pickwise.platform.crypto import KeyEncryptionKey
from pickwise.platform.events.relay import RelayResult, relay_batch
from pickwise.platform.files import processing
from pickwise.platform.notifications.email import QueuedEmail, RenderedEmail, send_queued
from pickwise.platform.provisioning.service import add_member, ensure_user, provision_tenant
from pickwise.platform.scanning import Scanner, build_scanner
from pickwise.platform.webhooks import delivery
from pickwise.platform.webhooks.ssrf import Resolver, system_resolver
from pickwise.shared.db import Database, ops_task
from pickwise.shared.settings import Settings

PASSWORD = "correct horse battery staple"
_PASSWORD_HASH = hash_password(PASSWORD)
_ip_counter = itertools.count(1)


@dataclass(frozen=True, slots=True)
class Account:
    email: str
    user_id: uuid.UUID
    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    role_key: str
    password: str = PASSWORD


@dataclass(frozen=True, slots=True)
class Tenant:
    id: uuid.UUID
    slug: str
    admin: Account


@dataclass(frozen=True, slots=True)
class Mail:
    to: str
    subject: str
    text: str


@dataclass
class Mailbox:
    """Captures dispatched emails and 'sends' them without SMTP (Mailpit has its own test)."""

    env: "Env"
    pending: list[QueuedEmail] = field(default_factory=list)
    sent: list[Mail] = field(default_factory=list)

    async def dispatcher(self, email: QueuedEmail) -> None:
        self.pending.append(email)

    def smtp(self, _settings: Settings, to_address: str, rendered: RenderedEmail) -> None:
        self.sent.append(Mail(to_address, rendered.subject, rendered.text))

    async def deliver(self) -> list[Mail]:
        pending, self.pending = self.pending, []
        for email in pending:
            await self.env.send(email)
        return self.sent

    async def token_for(self, to: str, *, template_word: str | None = None) -> str:
        """The link token in the newest email to ``to``."""
        await self.deliver()
        for mail in reversed(self.sent):
            if mail.to == to and (template_word is None or template_word in mail.text):
                match = re.search(r"https?://\S+?[/=]([A-Za-z0-9_-]{20,})(?:\s|$)", mail.text)
                assert match, mail.text
                return match.group(1)
        raise AssertionError(f"no email to {to}")

    def count_to(self, to: str) -> int:
        return sum(1 for m in self.sent if m.to == to)


class Env:
    def __init__(
        self, settings: Settings, worker_db: Database, kek: KeyEncryptionKey, app: FastAPI
    ) -> None:
        self.settings = settings
        self.db = worker_db
        self.kek = kek
        self.app = app
        self.tenant_ids: list[uuid.UUID] = []
        self.mailbox = Mailbox(self)
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    @ops_task
    async def send(self, email: QueuedEmail) -> str:
        async with self.db.ops_session() as s:
            return await send_queued(s, self.settings, self.kek, email.outbox_id, email.tenant_id)

    @ops_task
    async def scan_file(
        self, tenant_id: uuid.UUID, file_id: uuid.UUID, scanner: Scanner | None = None
    ) -> processing.Outcome:
        """What the worker's scan_file task does, with this environment's worker login."""
        async with storage.s3_client(self.settings) as s3:
            async with self.db.ops_session() as s:
                outcome = await processing.scan_file(
                    s,
                    self.settings,
                    scanner or build_scanner(self.settings),
                    s3,
                    tenant_id,
                    file_id,
                )
            if outcome.cleanup is not None:
                await storage.delete_object(s3, *outcome.cleanup)
        return outcome

    @ops_task
    async def relay(self) -> RelayResult:
        """What the worker's relay_events task does."""
        async with self.db.ops_session() as s:
            return await relay_batch(s, self.db)

    async def deliver(
        self,
        tenant_id: uuid.UUID,
        delivery_id: uuid.UUID,
        client: httpx.AsyncClient,
        *,
        resolver: Resolver = system_resolver,
        now: datetime.datetime | None = None,
    ) -> str:
        """What the worker's deliver_webhook task does, with a caller-supplied HTTP client."""
        return await delivery.deliver_one(
            self.db,
            self.settings,
            self.kek,
            client,
            tenant_id,
            delivery_id,
            resolver=resolver,
            now=now,
        )

    @ops_task
    async def sql(self, statement: str, **params: Any) -> list[tuple[Any, ...]]:
        """Run SQL as pickwise_ops (sees every tenant). Returns rows, if any."""
        async with self.db.ops_session() as s:
            result = await s.execute(text(statement), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else []  # type: ignore[attr-defined]

    @ops_task
    async def create_tenant(
        self, *, mfa_required: bool = False, settings: dict[str, object] | None = None
    ) -> Tenant:
        slug = f"t-{uuid.uuid4().hex[:10]}"
        admin_email = f"admin@{slug}.test"
        tenant_settings: dict[str, object] = {"mfa_required": mfa_required, **(settings or {})}
        async with self.db.ops_session() as s:
            result = await provision_tenant(
                s,
                self.settings,
                self.kek,
                slug=slug,
                name=f"Tenant {slug}",
                admin_email=admin_email,
                admin_name="Admin",
                tenant_settings=tenant_settings,
                send_invite=False,
            )
            await s.execute(
                text(
                    "UPDATE platform.users SET password_hash = :h, "
                    "email_verified_at = now() WHERE id = :u"
                ),
                {"h": _PASSWORD_HASH, "u": result.admin_user_id},
            )
            await s.execute(
                text(
                    "UPDATE platform.memberships SET status = 'active', joined_at = now() "
                    "WHERE id = :m"
                ),
                {"m": result.admin_membership_id},
            )
        self.tenant_ids.append(result.tenant_id)
        return Tenant(
            result.tenant_id,
            slug,
            Account(
                admin_email,
                result.admin_user_id,
                result.admin_membership_id,
                result.tenant_id,
                "tenant_admin",
            ),
        )

    @ops_task
    async def add_account(
        self,
        tenant: Tenant,
        role_key: str,
        *,
        email: str | None = None,
        status: str = "active",
        user_id: uuid.UUID | None = None,
    ) -> Account:
        email = email or f"{role_key}-{uuid.uuid4().hex[:8]}@{tenant.slug}.test"
        async with self.db.ops_session() as s:
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)}
            )
            uid = user_id or await ensure_user(s, email, role_key.replace("_", " ").title())
            membership = await add_member(s, tenant.id, uid, role_key, status=status)
            await s.execute(
                text(
                    "UPDATE platform.users SET password_hash = coalesce(password_hash, :h), "
                    "email_verified_at = coalesce(email_verified_at, now()) WHERE id = :u"
                ),
                {"h": _PASSWORD_HASH, "u": uid},
            )
        return Account(email, uid, membership, tenant.id, role_key)

    async def defer_job(self, task_name: str, **kwargs: Any) -> None:
        self.jobs.append((task_name, kwargs))

    @ops_task
    async def purge_all(self) -> None:
        for tenant_id in dict.fromkeys(self.tenant_ids):
            async with self.db.ops_session() as s:
                await s.execute(
                    text(
                        "UPDATE platform.tenants SET status = 'closed', closed_at = now() "
                        "WHERE id = :t"
                    ),
                    {"t": tenant_id},
                )
                info = (
                    await s.execute(
                        text("SELECT slug, status FROM platform.tenants WHERE id = :t"),
                        {"t": tenant_id},
                    )
                ).all()
                if not info:  # e.g. db/tests/test_migrate rebuilt the schema meanwhile
                    continue
                await s.execute(text("SELECT * FROM platform.purge_tenant(:t)"), {"t": tenant_id})


class Api:
    """One browser-like client with its own cookie jar and its own client IP."""

    def __init__(self, app: FastAPI, settings: Settings, *, ip: str | None = None) -> None:
        self.settings = settings
        self.ip = ip or f"2001:db8::{next(_ip_counter):x}"
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(self.ip, 5000)),
            base_url="http://test",
        )
        self.cookie_name = "pw_session"
        self.csrf_name = "pw_csrf"

    async def aclose(self) -> None:
        await self.http.aclose()

    async def with_csrf(self) -> "Api":
        token = (await self.http.get("/v1/auth/csrf")).json()["csrf_token"]
        self.http.headers["X-CSRF-Token"] = token
        return self

    @property
    def session_cookie(self) -> str | None:
        return self.http.cookies.get(self.cookie_name)

    async def get(self, path: str, **kw: Any) -> httpx.Response:
        return await self.http.get(path, **kw)

    async def post(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return await self.http.post(path, json=json, **kw)

    async def put(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return await self.http.put(path, json=json, **kw)

    async def patch(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return await self.http.patch(path, json=json, **kw)

    async def delete(self, path: str, **kw: Any) -> httpx.Response:
        return await self.http.delete(path, **kw)

    async def login(self, email: str, password: str = PASSWORD) -> httpx.Response:
        return await self.post("/v1/auth/login", {"email": email, "password": password})

    async def sign_in(self, account: Account) -> "Api":
        response = await self.login(account.email, account.password)
        assert response.status_code == 200, response.text
        return self


def totp(secret: str, offset_steps: int = 0) -> str:
    import time

    return pyotp.TOTP(secret).at(int(time.time()) + 30 * offset_steps)
