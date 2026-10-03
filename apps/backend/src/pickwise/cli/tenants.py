# SPDX-License-Identifier: AGPL-3.0-only
"""`pickwise tenant …` and `pickwise demo seed`: ops entry points run as pickwise_maint."""

import asyncio
import uuid
from typing import Annotated

import typer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.passwords import hash_password
from pickwise.platform.crypto import kek_from_settings
from pickwise.platform.provisioning import platform_defaults  # noqa: F401 - registers the hook
from pickwise.platform.provisioning.service import (
    ProvisioningError,
    add_member,
    ensure_user,
    provision_tenant,
    sync_defaults,
)
from pickwise.shared.db import Database, ops_command
from pickwise.shared.settings import get_settings

tenant_app = typer.Typer(no_args_is_help=True, help="Tenant provisioning (run as pickwise_maint).")


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@tenant_app.command("create")
@ops_command
def tenant_create(
    slug: Annotated[str, typer.Option(help="URL-safe id, 3-40 chars of a-z 0-9 -")],
    name: Annotated[str, typer.Option(help="Display name")],
    admin_email: Annotated[str, typer.Option(help="First admin; receives an invite email")],
    admin_name: Annotated[str, typer.Option(help="First admin's display name")] = "Administrator",
) -> None:
    """Provision a tenant: keys, default roles, first admin and their invite."""
    settings = get_settings()

    async def run() -> str:
        database = Database.from_settings(settings)
        try:
            async with database.ops_session() as session:
                result = await provision_tenant(
                    session,
                    settings,
                    kek_from_settings(settings),
                    slug=slug,
                    name=name,
                    admin_email=admin_email,
                    admin_name=admin_name,
                )
            return str(result.tenant_id)
        finally:
            await database.dispose()

    try:
        tenant_id = asyncio.run(run())
    except ProvisioningError as exc:
        _fail(str(exc))
        return
    typer.echo(f"tenant {slug} created ({tenant_id}); invite queued for the admin")


@tenant_app.command("sync-defaults")
@ops_command
def tenant_sync_defaults() -> None:
    """Re-run every module's provisioning hook for every tenant (adds only what's missing)."""

    async def run() -> int:
        database = Database.from_settings(get_settings())
        try:
            async with database.ops_session() as session:
                return len(await sync_defaults(session))
        finally:
            await database.dispose()

    typer.echo(f"defaults synced for {asyncio.run(run())} tenant(s)")


DEMO_TENANTS = (("acme", "Acme Industries"), ("globex", "Globex Corporation"))
MULTI_TENANT_USER = ("priya.sharma@pickwise.test", "Priya Sharma")


@ops_command
def seed_demo() -> list[str]:
    """Idempotent demo data for development and tests. Returns the login emails."""
    settings = get_settings()
    password = settings.demo_password.get_secret_value()
    if not password:
        _fail("DEMO_PASSWORD is not set (run `make dev` to generate .env)")
    password_hash = hash_password(password)

    async def run() -> list[str]:
        database = Database.from_settings(settings)
        logins: list[str] = []
        try:
            async with database.ops_session() as session:
                kek = kek_from_settings(settings)
                tenant_ids: dict[str, uuid.UUID] = {}
                for slug, name in DEMO_TENANTS:
                    existing = (
                        await session.execute(
                            text("SELECT id FROM platform.tenants WHERE slug = :s"), {"s": slug}
                        )
                    ).scalar_one_or_none()
                    admin_email = f"admin@{slug}.test"
                    if existing is None:
                        result = await provision_tenant(
                            session,
                            settings,
                            kek,
                            slug=slug,
                            name=name,
                            admin_email=admin_email,
                            admin_name=f"{name} Admin",
                            tenant_settings={"demo": True},
                            send_invite=False,
                        )
                        existing = result.tenant_id
                    tenant_ids[slug] = existing
                    logins.append(admin_email)
                    await _activate(session, existing, admin_email, password_hash)

                email, display_name = MULTI_TENANT_USER
                user_id = await ensure_user(session, email, display_name)
                for tenant_id in tenant_ids.values():
                    await session.execute(
                        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
                    )
                    await add_member(session, tenant_id, user_id, "hr_admin", status="active")
                await _set_password(session, user_id, password_hash)
                logins.append(email)
        finally:
            await database.dispose()
        return logins

    return asyncio.run(run())


async def _activate(
    session: AsyncSession, tenant_id: uuid.UUID, email: str, password_hash: str
) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
    )
    user_id: uuid.UUID = (
        await session.execute(text("SELECT id FROM platform.users WHERE email = :e"), {"e": email})
    ).scalar_one()
    await session.execute(
        text(
            "UPDATE platform.memberships SET status = 'active', "
            "joined_at = coalesce(joined_at, now()) "
            "WHERE tenant_id = :t AND user_id = :u AND status <> 'active'"
        ),
        {"t": tenant_id, "u": user_id},
    )
    await _set_password(session, user_id, password_hash)


async def _set_password(session: AsyncSession, user_id: uuid.UUID, password_hash: str) -> None:
    await session.execute(
        text(
            "UPDATE platform.users SET password_hash = :h, "
            "email_verified_at = coalesce(email_verified_at, now()) "
            "WHERE id = :u"
        ),
        {"h": password_hash, "u": user_id},
    )
