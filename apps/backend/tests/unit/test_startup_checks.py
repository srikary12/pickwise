# SPDX-License-Identifier: AGPL-3.0-only
import base64
import os
from typing import Any

import pytest
from pydantic import SecretStr

from pickwise.api.app import create_app
from pickwise.platform.startup_checks import UnsafeConfigurationError, check_settings
from pickwise.shared.settings import Environment, ScannerKind, Settings


def production(**overrides: Any) -> Settings:
    """A production configuration that passes every check, unless overridden."""
    values: dict[str, Any] = {
        "pickwise_env": Environment.PRODUCTION,
        "scanner": ScannerKind.CLAMAV,
        "session_secret": SecretStr("s" * 48),
        "pickwise_kek": SecretStr(base64.b64encode(os.urandom(32)).decode()),
        "public_base_url": "https://hr.example.com",
        # The test stack sets DEMO_PASSWORD in the environment; production must not.
        "demo_password": SecretStr(""),
    }
    values.update(overrides)
    return Settings(**values)


def test_a_complete_production_configuration_passes() -> None:
    check_settings(production())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"scanner": ScannerKind.STUB}, "SCANNER=stub"),
        ({"session_secret": SecretStr("short")}, "SESSION_SECRET"),
        ({"pickwise_kek": SecretStr("")}, "PICKWISE_KEK"),
        ({"public_base_url": "http://hr.example.com"}, "https://"),
        ({"demo_password": SecretStr("demo-123")}, "DEMO_PASSWORD"),
    ],
)
def test_production_refuses_unsafe_settings(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(UnsafeConfigurationError, match=message):
        check_settings(production(**overrides))


def test_api_refuses_to_boot_with_stub_in_production() -> None:
    with pytest.raises(UnsafeConfigurationError):
        create_app(production(scanner=ScannerKind.STUB))


@pytest.mark.parametrize("env", [Environment.DEVELOPMENT, Environment.TEST])
def test_development_and_test_allow_the_stub_and_dev_secrets(env: Environment) -> None:
    check_settings(Settings(pickwise_env=env, scanner=ScannerKind.STUB))


def test_production_hides_interactive_docs() -> None:
    assert create_app(production()).docs_url is None
