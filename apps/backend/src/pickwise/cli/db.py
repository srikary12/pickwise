# SPDX-License-Identifier: AGPL-3.0-only
"""Database bootstrap verification, migrations and reference-data seeding.

This is what the one-shot ``migrate`` container runs (``pickwise db migrate``).
"""

import os
from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

from pickwise.platform.crypto import kek_from_settings
from pickwise.platform.crypto.keys import platform_key_aad, wrap_new_key
from pickwise.platform.permissions import PERMISSIONS
from pickwise.shared.logging import get_logger
from pickwise.shared.settings import BootstrapSettings, Settings, get_settings

log = get_logger("pickwise.db")


def alembic_ini() -> Path:
    """alembic.ini sits in apps/backend, the working directory of every backend container."""
    return Path(os.environ.get("PICKWISE_ALEMBIC_INI", "alembic.ini")).resolve()


PASSWORD_MISMATCH = (
    "role passwords don't match this database volume: run `make reset` "  # noqa: S105
    "(the Postgres volume was initialised with different credentials than the current .env)"
)

GROUP_ROLES = ("pickwise_owner", "pickwise_app", "pickwise_ops")
LOGIN_USERS = ("pickwise_migrator", "pickwise_api", "pickwise_worker", "pickwise_maint")


@dataclass(frozen=True, slots=True)
class Membership:
    member: str
    role: str
    inherit: bool
    set_option: bool


# CLAUDE.md rule 4. pickwise_ops is held WITH INHERIT FALSE so its policies apply
# only after an explicit SET LOCAL ROLE.
EXPECTED_MEMBERSHIPS = frozenset(
    {
        Membership("pickwise_migrator", "pickwise_owner", inherit=True, set_option=True),
        Membership("pickwise_api", "pickwise_app", inherit=True, set_option=True),
        Membership("pickwise_worker", "pickwise_app", inherit=True, set_option=True),
        Membership("pickwise_worker", "pickwise_ops", inherit=False, set_option=True),
        Membership("pickwise_maint", "pickwise_ops", inherit=False, set_option=True),
    }
)


# db/bootstrap/00_roles.sql sets these per login user (ALTER ROLE … SET).
EXPECTED_ROLE_SETTINGS: dict[str, dict[str, str]] = {
    "pickwise_api": {"statement_timeout": "30s", "idle_in_transaction_session_timeout": "60s"},
    "pickwise_worker": {
        "statement_timeout": "10min",
        "idle_in_transaction_session_timeout": "60s",
    },
    "pickwise_maint": {"statement_timeout": "0", "idle_in_transaction_session_timeout": "10min"},
    "pickwise_migrator": {
        "role": "pickwise_owner",
        "statement_timeout": "0",
        "idle_in_transaction_session_timeout": "10min",
    },
}


class BootstrapError(RuntimeError):
    pass


def conninfo_for(settings: Settings, user: str, password: str) -> str:
    return psycopg.conninfo.make_conninfo(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=user,
        password=password,
        connect_timeout=10,
        application_name="pickwise-migrate",
    )


def verify_logins(settings: Settings, bootstrap: BootstrapSettings) -> None:
    """Every login user must be able to connect with the credentials in .env."""
    failed: list[str] = []
    for user, secret in bootstrap.login_passwords().items():
        password = secret.get_secret_value()
        if not password:
            raise BootstrapError(
                f"no password configured for {user}: run `make dev` to generate .env"
            )
        try:
            with psycopg.connect(conninfo_for(settings, user, password)) as conn:
                conn.execute("SELECT 1")
        except psycopg.OperationalError as exc:
            if "password authentication failed" in str(exc) or "does not exist" in str(exc):
                failed.append(user)
            else:
                raise
    if failed:
        raise BootstrapError(f"{PASSWORD_MISMATCH}; failed: {', '.join(failed)}")


def verify_role_model(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Assert the bootstrap produced exactly the role model in CLAUDE.md rule 4."""
    problems: list[str] = []
    rows = conn.execute(
        "SELECT rolname, rolcanlogin, rolbypassrls, rolsuper FROM pg_roles "
        "WHERE rolname LIKE 'pickwise\\_%'"
    ).fetchall()
    roles = {str(r[0]): (bool(r[1]), bool(r[2]), bool(r[3])) for r in rows}

    for name in (*GROUP_ROLES, *LOGIN_USERS):
        if name not in roles:
            problems.append(f"missing role {name}")
    for name, (can_login, bypass_rls, superuser) in roles.items():
        if bypass_rls:
            problems.append(f"{name} has BYPASSRLS (no role may have it)")
        if superuser:
            problems.append(f"{name} is a superuser")
        if name in GROUP_ROLES and can_login:
            problems.append(f"group role {name} must be NOLOGIN")
        if name in LOGIN_USERS and not can_login:
            problems.append(f"login user {name} cannot log in")

    member_rows = conn.execute(
        "SELECT m.rolname, r.rolname, am.inherit_option, am.set_option "
        "FROM pg_auth_members am "
        "JOIN pg_roles m ON m.oid = am.member "
        "JOIN pg_roles r ON r.oid = am.roleid "
        "WHERE m.rolname LIKE 'pickwise\\_%' AND r.rolname LIKE 'pickwise\\_%'"
    ).fetchall()
    actual = {Membership(str(a), str(b), bool(c), bool(d)) for a, b, c, d in member_rows}
    for missing in sorted(EXPECTED_MEMBERSHIPS - actual, key=str):
        problems.append(f"missing or wrong membership: {missing}")
    for extra in sorted(actual - EXPECTED_MEMBERSHIPS, key=str):
        problems.append(f"unexpected membership: {extra}")

    setting_rows = conn.execute(
        "SELECT r.rolname, unnest(s.setconfig) FROM pg_db_role_setting s "
        "JOIN pg_roles r ON r.oid = s.setrole WHERE s.setdatabase = 0 "
        "AND r.rolname LIKE 'pickwise\\_%'"
    ).fetchall()
    actual_settings: dict[str, dict[str, str]] = {}
    for role, setting in setting_rows:
        key, _, value = str(setting).partition("=")
        actual_settings.setdefault(str(role), {})[key] = value
    for role, expected in EXPECTED_ROLE_SETTINGS.items():
        for key, value in expected.items():
            got = actual_settings.get(role, {}).get(key)
            if got != value:
                problems.append(
                    f"{role} has {key}={got!r}, expected {value!r} "
                    "(an older database volume: run `make reset`)"
                )

    if problems:
        raise BootstrapError("database role model is wrong:\n  - " + "\n  - ".join(problems))


def run_migrations(revision: str = "head") -> None:
    cfg = Config(str(alembic_ini()))
    command.upgrade(cfg, revision)


# Reference-data seeds. Each is a no-op until the phase that creates its table.
SEED_STEPS: tuple[tuple[str, str], ...] = (
    ("platform data key", "platform.platform_keys"),
    ("permission catalog", "platform.permissions"),
    ("statutory rule sets", "payroll.statutory_rule_sets"),
)


def sync_permissions(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Make platform.permissions match the catalog in code (upsert, then delete the rest)."""
    catalog = PERMISSIONS.all()
    codes = [p.code for p in catalog]
    with conn.transaction():
        for p in catalog:
            conn.execute(
                "INSERT INTO platform.permissions (code, module, description, is_sensitive) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (code) DO UPDATE SET "
                "module = EXCLUDED.module, description = EXCLUDED.description, "
                "is_sensitive = EXCLUDED.is_sensitive",
                (p.code, p.module, p.description, p.is_sensitive),
            )
        removed = conn.execute(
            "DELETE FROM platform.permissions WHERE NOT (code = ANY(%s))", (codes,)
        ).rowcount
    log.info("permission catalog synced", permissions=len(codes), removed=removed)


def ensure_platform_data_key(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Create the first platform data key (wrapped by the KEK) if there is none."""
    row = conn.execute(
        "SELECT count(*) FROM platform.platform_keys WHERE purpose = 'data' AND status = 'active'"
    ).fetchone()
    if row and row[0]:
        log.info("platform data key present")
        return
    kek = kek_from_settings(get_settings())
    _material, wrapped = wrap_new_key(kek, platform_key_aad("data", 1))
    with conn.transaction():
        conn.execute(
            "INSERT INTO platform.platform_keys (purpose, version, wrapped_key, kek_id) "
            "VALUES ('data', 1, %s, %s)",
            (wrapped, kek.kek_id),
        )
    log.info("platform data key created", version=1)


def seed_reference_data(conn: psycopg.Connection[tuple[object, ...]]) -> list[str]:
    seeders = {
        "platform.platform_keys": ensure_platform_data_key,
        "platform.permissions": sync_permissions,
    }
    ran: list[str] = []
    for label, table in SEED_STEPS:
        exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()
        if not exists or not exists[0]:
            log.info("seed skipped: table not present yet", step=label, table=table)
            continue
        seeder = seeders.get(table)
        if seeder is None:
            raise BootstrapError(f"{table} exists but no seeder is registered for {label}")
        seeder(conn)
        ran.append(label)
    return ran


def migrate(settings: Settings, bootstrap: BootstrapSettings) -> None:
    verify_logins(settings, bootstrap)
    log.info("all login users can connect")
    migrator = bootstrap.pg_migrator_password.get_secret_value()
    with psycopg.connect(conninfo_for(settings, "pickwise_migrator", migrator)) as conn:
        verify_role_model(conn)
    log.info("role model verified")
    run_migrations()
    log.info("migrations applied")
    with psycopg.connect(conninfo_for(settings, "pickwise_migrator", migrator)) as conn:
        seed_reference_data(conn)
    log.info("migrate finished")
