# SPDX-License-Identifier: AGPL-3.0-only
"""`pickwise keys …`: key rotation (ADR 0014). Ops entry points, run as pickwise_maint.

KEK rotation:   set PICKWISE_KEK_NEXT, run `keys rewrap`, then swap it into PICKWISE_KEK.
Data keys:      `keys rotate-data` → `keys reencrypt` (re-encrypts, then retires the old key).
Blind indexes:  `keys rotate-bidx` → `keys rebuild-bidx` (recomputes, then retires the old key).
"""

import asyncio
import uuid
from typing import Annotated

import typer
from sqlalchemy import text

from pickwise import wiring
from pickwise.platform import audit
from pickwise.platform.crypto import EnvKek, kek_from_settings
from pickwise.platform.crypto.keys import BLIND_INDEX, DATA, Keyring
from pickwise.platform.crypto.rotation import (
    ENCRYPTED_FIELDS,
    add_tenant_key_version,
    count_on_old_data_keys,
    rebuild_blind_index,
    reencrypt_field,
    reload_keyring,
    retire_rotating,
    rewrap_all,
)
from pickwise.shared.db import Database, ops_command
from pickwise.shared.settings import get_settings

keys_app = typer.Typer(no_args_is_help=True, help="Key rotation (run as pickwise_maint).")

TenantOption = Annotated[str | None, typer.Option("--tenant", help="Tenant slug")]
AllOption = Annotated[bool, typer.Option("--all", help="Every active tenant")]


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@ops_command
async def _tenants(database: Database, slug: str | None, every: bool) -> list[uuid.UUID]:
    if bool(slug) == every:
        _fail("give exactly one of --tenant <slug> or --all")
    async with database.ops_session() as session:
        if slug:
            rows = (
                await session.execute(
                    text("SELECT id FROM platform.tenants WHERE slug = :s"), {"s": slug.lower()}
                )
            ).all()
            if not rows:
                _fail(f"no tenant with slug {slug!r}")
        else:
            rows = (
                await session.execute(
                    text("SELECT id FROM platform.tenants WHERE status = 'active' ORDER BY id")
                )
            ).all()
    return [r[0] for r in rows]


@keys_app.command("rewrap")
@ops_command
def rewrap() -> None:
    """Re-wrap every wrapped key under PICKWISE_KEK_NEXT (no data is re-encrypted)."""
    settings = get_settings()
    next_value = settings.pickwise_kek_next.get_secret_value()
    if not next_value:
        _fail("set PICKWISE_KEK_NEXT to the new KEK (base64, 32 bytes)")

    async def run() -> dict[str, int]:
        database = Database.from_settings(settings)
        try:
            async with database.ops_session() as session:
                return await rewrap_all(
                    session, kek_from_settings(settings), EnvKek.from_base64(next_value)
                )
        finally:
            await database.dispose()

    counts = asyncio.run(run())
    typer.echo(
        f"re-wrapped {counts['platform_keys']} platform and {counts['tenant_keys']} tenant key(s). "
        "Now put PICKWISE_KEK_NEXT into PICKWISE_KEK and restart the services."
    )


@keys_app.command("rotate-data")
@ops_command
def rotate_data(tenant: TenantOption = None, all_tenants: AllOption = False) -> None:
    """Add a new data-key version; old versions stay readable until `keys reencrypt`."""
    _rotate(DATA, tenant, all_tenants)


@keys_app.command("rotate-bidx")
@ops_command
def rotate_bidx(tenant: TenantOption = None, all_tenants: AllOption = False) -> None:
    """Add a new blind-index key (lookups use every non-retired key until `keys rebuild-bidx`)."""
    _rotate(BLIND_INDEX, tenant, all_tenants)


@ops_command
def _rotate(purpose: str, tenant: str | None, every: bool) -> None:
    settings = get_settings()

    async def run() -> list[tuple[uuid.UUID, int]]:
        database = Database.from_settings(settings)
        out = []
        try:
            for tenant_id in await _tenants(database, tenant, every):
                async with database.ops_session() as session:
                    version = await add_tenant_key_version(
                        session, kek_from_settings(settings), tenant_id, purpose
                    )
                    await audit.record(session, f"key.rotated.{purpose}", "platform.tenant_keys")
                out.append((tenant_id, version))
        finally:
            await database.dispose()
        return out

    try:
        for tenant_id, version in asyncio.run(run()):
            typer.echo(f"tenant {tenant_id}: {purpose} key version {version} is now active")
    except RuntimeError as exc:
        _fail(str(exc))


@keys_app.command("reencrypt")
@ops_command
def reencrypt(tenant: TenantOption = None, all_tenants: AllOption = False) -> None:
    """Re-encrypt every registered column under the active data key, then retire the old key."""
    wiring.register_all()
    settings = get_settings()

    async def run() -> list[tuple[uuid.UUID, int, bool]]:
        database = Database.from_settings(settings)
        kek = kek_from_settings(settings)
        out = []
        try:
            for tenant_id in await _tenants(database, tenant, all_tenants):
                changed = 0
                for field in ENCRYPTED_FIELDS.all():
                    while True:
                        async with database.ops_session() as session:
                            keyring: Keyring = await reload_keyring(session, kek, tenant_id)
                            n = await reencrypt_field(session, keyring, tenant_id, field)
                        changed += n
                        if n == 0:
                            break
                async with database.ops_session() as session:
                    keyring = await reload_keyring(session, kek, tenant_id)
                    active = keyring.active_key(DATA).version
                    remaining = 0
                    for field in ENCRYPTED_FIELDS.all():
                        remaining += await count_on_old_data_keys(session, tenant_id, field, active)
                    retired = False
                    if remaining == 0:
                        retired = bool(await retire_rotating(session, tenant_id, DATA))
                out.append((tenant_id, changed, retired))
        finally:
            await database.dispose()
        return out

    for tenant_id, changed, retired in asyncio.run(run()):
        typer.echo(
            f"tenant {tenant_id}: re-encrypted {changed} value(s)"
            + ("; old data key retired" if retired else "")
        )


@keys_app.command("rebuild-bidx")
@ops_command
def rebuild_bidx(tenant: TenantOption = None, all_tenants: AllOption = False) -> None:
    """Recompute every blind index under the active key, then retire the old key."""
    wiring.register_all()
    settings = get_settings()

    async def run() -> list[tuple[uuid.UUID, int]]:
        database = Database.from_settings(settings)
        kek = kek_from_settings(settings)
        out = []
        try:
            for tenant_id in await _tenants(database, tenant, all_tenants):
                rows = 0
                async with database.ops_session() as session:
                    keyring = await reload_keyring(session, kek, tenant_id)
                    for field in ENCRYPTED_FIELDS.all():
                        rows += await rebuild_blind_index(session, keyring, tenant_id, field)
                    await retire_rotating(session, tenant_id, BLIND_INDEX)
                out.append((tenant_id, rows))
        finally:
            await database.dispose()
        return out

    for tenant_id, rows in asyncio.run(run()):
        typer.echo(f"tenant {tenant_id}: rebuilt {rows} blind index value(s); old key retired")
