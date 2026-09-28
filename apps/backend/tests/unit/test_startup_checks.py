# SPDX-License-Identifier: AGPL-3.0-only
import pytest

from pickwise.api.app import create_app
from pickwise.platform.startup_checks import UnsafeConfigurationError, check_settings
from pickwise.shared.settings import Environment, ScannerKind, Settings


def test_production_refuses_the_stub_scanner() -> None:
    with pytest.raises(UnsafeConfigurationError, match="SCANNER=stub"):
        check_settings(Settings(pickwise_env=Environment.PRODUCTION, scanner=ScannerKind.STUB))


def test_api_refuses_to_boot_with_stub_in_production() -> None:
    with pytest.raises(UnsafeConfigurationError):
        create_app(Settings(pickwise_env=Environment.PRODUCTION, scanner=ScannerKind.STUB))


@pytest.mark.parametrize(
    ("env", "scanner"),
    [
        (Environment.PRODUCTION, ScannerKind.CLAMAV),
        (Environment.DEVELOPMENT, ScannerKind.STUB),
        (Environment.TEST, ScannerKind.STUB),
    ],
)
def test_allowed_combinations(env: Environment, scanner: ScannerKind) -> None:
    check_settings(Settings(pickwise_env=env, scanner=scanner))


def test_production_hides_interactive_docs() -> None:
    app = create_app(Settings(pickwise_env=Environment.PRODUCTION, scanner=ScannerKind.CLAMAV))
    assert app.docs_url is None
