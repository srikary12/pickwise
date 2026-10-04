# SPDX-License-Identifier: AGPL-3.0-only
"""Erasure handlers: how each module removes or anonymises a data subject's personal data.

Every module that holds personal data registers one (core: the employee master; recruit:
candidates, applications and resumes; payroll: anonymise, because the law requires the
records be kept). The runner calls them in ``order`` inside one transaction, in purge mode,
as ``pickwise_ops``, so the whole erasure happens or none of it does.

A handler:
- deletes or anonymises the subject's rows (it may delete from append-only tables: purge
  mode allows it);
- deletes the subject's objects from storage through ``ctx.s3``. A storage failure aborts and
  rolls the database work back; objects already deleted stay deleted, so the erasure can
  simply be run again and finish;
- reports the audit rows it left behind with ``ctx.scrub``: erasure then blanks their
  diffs through ``audit.scrub_subject``, because the row-change trail would otherwise keep
  the personal data it was meant to forget;
- returns how many rows it changed, by name. Counts only, never values.

Handlers must be idempotent: running an erasure twice must be harmless.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

SubjectType = Literal["employee", "candidate"]


@dataclass(frozen=True, slots=True)
class ErasureSubject:
    tenant_id: uuid.UUID
    subject_type: SubjectType
    subject_id: uuid.UUID
    # The person's login, if they had one: their notifications are theirs.
    user_id: uuid.UUID | None = None
    # Their email address, to find mail sent to them. Held in memory only; never logged.
    email: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ScrubTarget:
    entity_table: str  # schema-qualified, e.g. 'core.employees'
    entity_id: uuid.UUID


@dataclass(slots=True)
class ErasureContext:
    session: AsyncSession  # an ops session in purge mode, tenant context set
    subject: ErasureSubject
    s3: Any  # an S3 client (aioboto3) for deleting objects
    scrub_targets: list[ScrubTarget] = field(default_factory=list)

    def scrub(self, entity_table: str, entity_id: uuid.UUID) -> None:
        self.scrub_targets.append(ScrubTarget(entity_table, entity_id))


ErasureHandler = Callable[[ErasureContext], Awaitable[dict[str, int]]]


@dataclass(frozen=True, slots=True)
class _Registration:
    name: str
    order: int
    handler: ErasureHandler


class ErasureHandlerRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(self, name: str, handler: ErasureHandler, *, order: int = 100) -> None:
        """``name`` keys the handler's counts in the report, e.g. 'platform' or 'core.employees'.
        Lower ``order`` runs first; use it when one module's rows point at another's."""
        existing = self._registrations.get(name)
        if existing is not None and existing.handler is not handler:
            raise ValueError(f"erasure handler {name!r} registered twice")
        self._registrations[name] = _Registration(name, order, handler)

    def unregister(self, name: str) -> None:
        self._registrations.pop(name, None)

    def handlers(self) -> list[tuple[str, ErasureHandler]]:
        ordered = sorted(self._registrations.values(), key=lambda r: (r.order, r.name))
        return [(r.name, r.handler) for r in ordered]


ERASURE_HANDLERS = ErasureHandlerRegistry()
