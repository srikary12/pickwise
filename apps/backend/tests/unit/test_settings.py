# SPDX-License-Identifier: AGPL-3.0-only
from pydantic import SecretStr

from pickwise.shared.settings import Settings


def test_database_urls_escape_credentials() -> None:
    settings = Settings(database_user="pickwise_api", database_password=SecretStr("p@ss/w:rd"))
    assert settings.sqlalchemy_url() == (
        "postgresql+asyncpg://pickwise_api:p%40ss%2Fw%3Ard@postgres:5432/pickwise"
    )
    assert settings.psycopg_conninfo().startswith("postgresql://pickwise_api:p%40ss%2Fw%3Ard@")


def test_secrets_are_not_rendered_in_repr() -> None:
    settings = Settings(database_password=SecretStr("hunter2"))
    assert "hunter2" not in repr(settings)
