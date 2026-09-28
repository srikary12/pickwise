# SPDX-License-Identifier: AGPL-3.0-only
"""Process configuration, loaded from environment variables.

Each process gets only the credentials it needs: the API container receives the
pickwise_api password, the worker the pickwise_worker password, and so on. The
migrate container additionally receives every login password so it can verify
them (see ``BootstrapSettings``).
"""

from enum import StrEnum
from functools import lru_cache
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ScannerKind(StrEnum):
    STUB = "stub"
    CLAMAV = "clamav"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore", frozen=True)

    pickwise_env: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"

    database_host: str = "postgres"
    database_port: int = 5432
    database_name: str = "pickwise"
    database_user: str = "pickwise_api"
    database_password: SecretStr = SecretStr("")

    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_bucket_files: str = "pickwise-files"
    s3_bucket_quarantine: str = "pickwise-quarantine"

    scanner: ScannerKind = ScannerKind.STUB
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = Field(default=30.0, gt=0)

    readiness_timeout_seconds: float = Field(default=3.0, gt=0)

    @property
    def is_production(self) -> bool:
        return self.pickwise_env is Environment.PRODUCTION

    def sqlalchemy_url(self) -> str:
        """asyncpg URL for SQLAlchemy (API and worker request traffic)."""
        return self._url("postgresql+asyncpg")

    def psycopg_conninfo(self) -> str:
        """libpq conninfo for psycopg (procrastinate, CLI)."""
        return self._url("postgresql")

    def _url(self, scheme: str) -> str:
        password = quote(self.database_password.get_secret_value(), safe="")
        user = quote(self.database_user, safe="")
        return (
            f"{scheme}://{user}:{password}@{self.database_host}:{self.database_port}"
            f"/{self.database_name}"
        )


class BootstrapSettings(BaseSettings):
    """Credentials the migrate container needs to verify every login user."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", frozen=True)

    pg_migrator_password: SecretStr = SecretStr("")
    pg_api_password: SecretStr = SecretStr("")
    pg_worker_password: SecretStr = SecretStr("")
    pg_maint_password: SecretStr = SecretStr("")

    def login_passwords(self) -> dict[str, SecretStr]:
        return {
            "pickwise_migrator": self.pg_migrator_password,
            "pickwise_api": self.pg_api_password,
            "pickwise_worker": self.pg_worker_password,
            "pickwise_maint": self.pg_maint_password,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
