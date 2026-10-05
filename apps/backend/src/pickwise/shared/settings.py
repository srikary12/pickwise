# SPDX-License-Identifier: AGPL-3.0-only
"""Process configuration, loaded from environment variables.

Each process gets only the credentials it needs: the API container receives the
pickwise_api password, the worker the pickwise_worker password, and so on. The
migrate container additionally receives every login password so it can verify
them (see ``BootstrapSettings``).
"""

import ipaddress
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
    # Presigned URLs are opened by browsers, which may reach the object store at a
    # different address than the API does (dev: localhost:8333). Defaults to s3_endpoint_url.
    s3_public_endpoint_url: str | None = None

    # File pipeline (ADR 0013).
    files_max_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    files_upload_ttl_seconds: int = Field(default=900, gt=0)
    files_download_ttl_seconds: int = Field(default=300, gt=0)
    files_allowed_mime_types: tuple[str, ...] = (
        "application/pdf",
        "image/png",
        "image/jpeg",
        "text/plain",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # --- bulk imports ---------------------------------------------------------------
    imports_max_rows: int = Field(default=20_000, gt=0)
    imports_max_errors: int = Field(default=5_000, gt=0)
    imports_batch_size: int = Field(default=500, gt=0)

    scanner: ScannerKind = ScannerKind.STUB
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = Field(default=30.0, gt=0)

    readiness_timeout_seconds: float = Field(default=3.0, gt=0)

    # --- webhooks -----------------------------------------------------------------
    # Webhook targets must be https and resolve only to public addresses (ADR 0017).
    # Dev and test set this to deliver to a receiver on the compose network; it also
    # allows plain http. The API and worker refuse to start with it in production.
    webhook_allow_private_targets: bool = False
    webhook_timeout_seconds: float = Field(default=10.0, gt=0)

    # --- crypto and sessions ------------------------------------------------
    # PICKWISE_KEK: the only global crypto secret (base64, 32 bytes). It wraps the
    # platform and tenant data keys (ADR 0006, CLAUDE.md "Crypto").
    pickwise_kek: SecretStr = SecretStr("")
    # Only for `pickwise keys rewrap`: the KEK to move every wrapped key to (ADR 0014).
    pickwise_kek_next: SecretStr = SecretStr("")
    # SESSION_SECRET: HMAC key for signed short-lived cookies (SSO state) and
    # rate-limit keys. Session tokens themselves are random, not signed.
    session_secret: SecretStr = SecretStr("")
    session_idle_minutes: int = Field(default=60, gt=0)
    session_absolute_hours: int = Field(default=12, gt=0)

    # The browser-facing origin; links in emails point here, and the API is
    # served under <public_base_url>/api (same origin, ADR 0010).
    public_base_url: str = "http://localhost:3000"
    # Comma-separated CIDRs whose X-Forwarded-For we trust (the reverse proxy).
    trusted_proxies: str = ""

    signup_enabled: bool = False
    breached_password_check: bool = False

    # --- email ----------------------------------------------------------------
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = False
    smtp_from: str = "Pickwise <no-reply@pickwise.localhost>"

    # Development/test only: the demo seed's login password.
    demo_password: SecretStr = SecretStr("")

    @property
    def is_production(self) -> bool:
        return self.pickwise_env is Environment.PRODUCTION

    @property
    def secure_cookies(self) -> bool:
        return self.public_base_url.startswith("https://")

    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [
            ipaddress.ip_network(part.strip(), strict=False)
            for part in self.trusted_proxies.split(",")
            if part.strip()
        ]

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
