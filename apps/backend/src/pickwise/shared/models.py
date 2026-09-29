# SPDX-License-Identifier: AGPL-3.0-only
"""SQLAlchemy declarative base and the column mixins of the tenant-table template
(DATA_MODEL §0). Migrations are hand-written; models mirror them."""

import datetime
import uuid

from sqlalchemy import MetaData, text
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "%(table_name)s_%(column_0_N_name)s",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TenantMixin:
    """Primary key (tenant_id, id); both default in the database."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("platform.current_tenant_id()")
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))


class AuditColumnsMixin:
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=text("now()"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        server_default=text("platform.current_user_id()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(server_default=text("now()"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column()


class VersionedMixin:
    """Optimistic concurrency (CLAUDE.md rule 10): a stale row_version raises
    StaleDataError, which the API maps to 409. The touch_row trigger also bumps
    row_version, to the same value SQLAlchemy writes."""

    row_version: Mapped[int] = mapped_column(server_default=text("1"))

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, object]:  # noqa: N805
        return {"version_id_col": cls.row_version}
