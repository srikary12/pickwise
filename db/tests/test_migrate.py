# SPDX-License-Identifier: AGPL-3.0-only
"""The migrate container: login verification and a reversible migration chain."""

from collections.abc import Callable

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr

from pickwise.cli.db import (
    PASSWORD_MISMATCH,
    BootstrapError,
    alembic_ini,
    verify_logins,
)
from pickwise.shared.settings import BootstrapSettings, Settings

Connect = Callable[[str], psycopg.Connection[tuple[object, ...]]]

pytestmark = pytest.mark.db


def test_verify_logins_accepts_the_env_credentials() -> None:
    verify_logins(Settings(), BootstrapSettings())


def test_verify_logins_explains_a_password_mismatch() -> None:
    wrong = BootstrapSettings(pg_api_password=SecretStr("not-the-password"))
    with pytest.raises(BootstrapError) as excinfo:
        verify_logins(Settings(), wrong)
    message = str(excinfo.value)
    assert PASSWORD_MISMATCH in message
    assert "make reset" in message
    assert "pickwise_api" in message


def test_migrations_round_trip(connect: Connect) -> None:
    cfg = Config(str(alembic_ini()))
    command.downgrade(cfg, "base")
    gone = (
        connect("pickwise_api")
        .execute("SELECT to_regnamespace('queue'), to_regnamespace('platform')")
        .fetchone()
    )
    assert gone == (None, None)
    command.upgrade(cfg, "head")
    api = connect("pickwise_api")
    row = api.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'queue'").fetchone()
    assert row is not None
    assert isinstance(row[0], int)
    assert row[0] >= 4
    restored = api.execute(
        "SELECT to_regprocedure('platform.list_memberships_for_user(uuid)') IS NOT NULL, "
        "to_regprocedure('platform.purge_tenant(uuid)') IS NOT NULL, "
        "(SELECT count(*) FROM pg_policies WHERE schemaname = 'platform' AND tablename = 'memberships'), "
        "(SELECT count(*) FROM pg_inherits WHERE inhparent = 'audit.events'::regclass) > 0"
    ).fetchone()
    assert restored == (True, True, 3, True)
