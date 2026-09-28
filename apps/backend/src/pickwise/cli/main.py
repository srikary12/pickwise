# SPDX-License-Identifier: AGPL-3.0-only
"""The ``pickwise`` command-line interface (a composition root, like api/ and worker/).

Commands that need cross-tenant access become ``@ops_command`` entry points from
Phase 1 (CLAUDE.md rule 4); nothing in Phase 0 reads tenant data.
"""

import asyncio
import json
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from pickwise.cli import db as db_cli
from pickwise.platform.scanning import ScannerError, build_scanner, eicar_bytes, iter_bytes
from pickwise.shared.logging import configure_logging, get_logger
from pickwise.shared.settings import BootstrapSettings, ScannerKind, Settings, get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)
db_app = typer.Typer(no_args_is_help=True, help="Database bootstrap, migrations and seeds.")
demo_app = typer.Typer(no_args_is_help=True, help="Demo data (development and test only).")
openapi_app = typer.Typer(no_args_is_help=True, help="OpenAPI schema.")
worker_app = typer.Typer(no_args_is_help=True, help="Worker utilities.")
app.add_typer(db_app, name="db")
app.add_typer(demo_app, name="demo")
app.add_typer(openapi_app, name="openapi")
app.add_typer(worker_app, name="worker")

log = get_logger("pickwise.cli")


@app.callback()
def _setup() -> None:
    configure_logging(get_settings().log_level)


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@db_app.command("migrate")
def db_migrate() -> None:
    """Verify logins and the role model, run `alembic upgrade head`, then seed reference data."""
    try:
        db_cli.migrate(get_settings(), BootstrapSettings())
    except db_cli.BootstrapError as exc:
        _fail(str(exc))


@db_app.command("verify-roles")
def db_verify_roles() -> None:
    """Check that every login user can connect and the role model matches CLAUDE.md rule 4."""
    settings, bootstrap = get_settings(), BootstrapSettings()
    try:
        db_cli.verify_logins(settings, bootstrap)
        migrator = bootstrap.pg_migrator_password.get_secret_value()
        info = db_cli.conninfo_for(settings, "pickwise_migrator", migrator)
        with psycopg.connect(info) as conn:
            db_cli.verify_role_model(conn)
    except db_cli.BootstrapError as exc:
        _fail(str(exc))
    typer.echo("role model OK")


@db_app.command("seed")
def db_seed() -> None:
    """Seed reference data (permission catalog, statutory rule sets) where the tables exist."""
    settings, bootstrap = get_settings(), BootstrapSettings()
    migrator = bootstrap.pg_migrator_password.get_secret_value()
    with psycopg.connect(db_cli.conninfo_for(settings, "pickwise_migrator", migrator)) as conn:
        db_cli.seed_reference_data(conn)


@demo_app.command("seed")
def demo_seed() -> None:
    """Create the demo tenants (acme, globex). Never runs in production."""
    settings = get_settings()
    if settings.is_production:
        _fail("demo data is never seeded when PICKWISE_ENV=production")
    with psycopg.connect(settings.psycopg_conninfo()) as conn:
        row = conn.execute("SELECT to_regclass('platform.tenants') IS NOT NULL").fetchone()
    if not row or not row[0]:
        typer.echo("demo seed skipped: platform.tenants doesn't exist yet (arrives in Phase 1)")
        return
    _fail("platform.tenants exists but the demo seeder hasn't been written yet")


@openapi_app.command("export")
def openapi_export(
    out: Annotated[Path, typer.Option(help="Where to write openapi.json")],
) -> None:
    """Write the OpenAPI schema without starting a server."""
    from pickwise.api.app import create_app

    schema = create_app(Settings(pickwise_env=get_settings().pickwise_env)).openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(f"wrote {out}")


@app.command("scan-check")
def scan_check(
    wait_seconds: Annotated[int, typer.Option(help="How long to wait for clamd")] = 600,
) -> None:
    """Stream EICAR through the ClamAV adapter and require an `infected` verdict."""
    scanner = build_scanner(get_settings(), ScannerKind.CLAMAV)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while not await scanner.ping():
            if loop.time() > deadline:
                _fail(f"clamd did not answer PING within {wait_seconds}s")
            await asyncio.sleep(5)
        infected = await scanner.scan(iter_bytes(eicar_bytes()))
        clean = await scanner.scan(iter_bytes(b"Pickwise scan-check: a harmless file\n"))
        typer.echo(f"EICAR  -> {infected.verdict.value} ({infected.signature})")
        typer.echo(f"benign -> {clean.verdict.value}")
        if not infected.infected:
            _fail("scan-check FAILED: clamd did not flag EICAR")
        if clean.infected:
            _fail("scan-check FAILED: clamd flagged a benign file")
        typer.secho("scan-check OK", fg=typer.colors.GREEN)

    try:
        asyncio.run(run())
    except ScannerError as exc:
        _fail(f"scan-check FAILED: {exc}")


@worker_app.command("health")
def worker_health(
    max_age_seconds: Annotated[int, typer.Option(help="Oldest acceptable heartbeat")] = 60,
) -> None:
    """Exit 0 if a procrastinate worker has sent a heartbeat recently (container healthcheck)."""
    settings = get_settings()
    try:
        with psycopg.connect(settings.psycopg_conninfo(), connect_timeout=5) as conn:
            row = conn.execute(
                "SELECT count(*) FROM queue.procrastinate_workers "
                "WHERE last_heartbeat > now() - make_interval(secs => %s)",
                (max_age_seconds,),
            ).fetchone()
    except psycopg.Error as exc:
        _fail(f"worker unhealthy: {type(exc).__name__}")
        return
    if not row or not row[0]:
        _fail("worker unhealthy: no recent heartbeat")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
