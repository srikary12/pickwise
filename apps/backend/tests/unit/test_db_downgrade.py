# SPDX-License-Identifier: AGPL-3.0-only
"""`pickwise db downgrade`: argument plumbing and the production guard."""

from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from pickwise.cli import db as db_cli
from pickwise.cli.main import app
from pickwise.shared.settings import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def fresh_settings() -> Iterator[None]:
    # get_settings() is cached; each test sets PICKWISE_ENV itself.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_to_one_step(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(db_cli, "run_downgrade", calls.append)
    monkeypatch.setenv("PICKWISE_ENV", "development")
    assert runner.invoke(app, ["db", "downgrade"]).exit_code == 0
    assert calls == ["-1"]


def test_passes_the_requested_target(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(db_cli, "run_downgrade", calls.append)
    monkeypatch.setenv("PICKWISE_ENV", "test")
    assert runner.invoke(app, ["db", "downgrade", "--to", "base"]).exit_code == 0
    assert calls == ["base"]


def test_refuses_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(db_cli, "run_downgrade", calls.append)
    monkeypatch.setenv("PICKWISE_ENV", "production")
    result = runner.invoke(app, ["db", "downgrade"])
    assert result.exit_code == 1
    assert "production" in result.output
    assert calls == []
