# SPDX-License-Identifier: AGPL-3.0-only
import os

import pytest
from hypothesis import settings as hypothesis_settings

from pickwise.shared.settings import Settings

# The source tree is mounted read-only in the test container; keep Hypothesis's
# example database in memory instead of warning about it.
hypothesis_settings.register_profile("pickwise", database=None)
hypothesis_settings.load_profile("pickwise")


def _stack_available() -> bool:
    """Integration tests need the compose.test.yml stack (make test)."""
    return bool(os.environ.get("PG_API_PASSWORD")) and bool(os.environ.get("S3_ACCESS_KEY_ID"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _stack_available():
        return
    skip = pytest.mark.skip(reason="needs the test stack: run `make test`")
    for item in items:
        if "db" in item.keywords or "s3" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def settings() -> Settings:
    return Settings()
