# SPDX-License-Identifier: AGPL-3.0-only
"""Registries through which modules plug into approvals without platform importing them.

- ``ApproverResolverRegistry``: turns an approver spec such as ``manager`` into user ids.
  Platform registers ``role:<key>`` and ``user:<id>``; core registers ``manager``,
  ``skip_level`` and ``dept_head`` once org data exists (Phase 5). An approver kind with no
  resolver resolves to nobody, and a step nobody can approve fails the request
  (fail closed: an approval never silently passes).
- ``ApprovalHandlerRegistry``: what a module does when a request for its entity type is
  decided. Handlers run in the same transaction as the decision, so the module's state
  and the approval can't diverge; a handler that raises rolls the decision back.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

ENTITY_TYPES = (
    "leave_request",
    "regularization",
    "requisition",
    "offer",
    "payroll_run",
    "compensation",
    "separation",
)


@dataclass(frozen=True, slots=True)
class ResolveContext:
    tenant_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    attributes: dict[str, Any]
    requested_by: uuid.UUID | None
    # The text after "kind:" in the spec ("hr_admin" for "role:hr_admin"), if any.
    argument: str | None = None
    # Set when escalating: the approver who didn't act. ``skip_level`` then means that
    # person's manager; otherwise it means the requester's manager's manager.
    escalating_from: uuid.UUID | None = None


ApproverResolver = Callable[[AsyncSession, ResolveContext], Awaitable[list[uuid.UUID]]]


class ApproverResolverRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[str, ApproverResolver] = {}

    def register(self, kind: str, resolver: ApproverResolver) -> None:
        existing = self._resolvers.get(kind)
        if existing is not None and existing is not resolver:
            raise ValueError(f"approver resolver {kind!r} registered twice")
        self._resolvers[kind] = resolver

    def unregister(self, kind: str) -> None:
        self._resolvers.pop(kind, None)

    def get(self, kind: str) -> ApproverResolver | None:
        return self._resolvers.get(kind)

    def kinds(self) -> list[str]:
        return sorted(self._resolvers)


APPROVER_RESOLVERS = ApproverResolverRegistry()


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """What a handler is told about a decided request."""

    request_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    status: str  # approved | rejected | cancelled
    requested_by: uuid.UUID | None
    decided_by: uuid.UUID | None  # None when the system decided (nothing does yet)
    comment: str | None
    attributes: dict[str, Any] = field(default_factory=dict)


ApprovalCallback = Callable[[AsyncSession, ApprovalDecision], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApprovalHandler:
    on_approved: ApprovalCallback
    on_rejected: ApprovalCallback
    # Cancelling is the requester withdrawing (or an admin). Optional: many entities just
    # go back to draft.
    on_cancelled: ApprovalCallback | None = None


class ApprovalHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ApprovalHandler] = {}

    def register(self, entity_type: str, handler: ApprovalHandler) -> None:
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"unknown approval entity type {entity_type!r}")
        existing = self._handlers.get(entity_type)
        if existing is not None and existing != handler:
            raise ValueError(f"approval handler for {entity_type!r} registered twice")
        self._handlers[entity_type] = handler

    def unregister(self, entity_type: str) -> None:
        self._handlers.pop(entity_type, None)

    def get(self, entity_type: str) -> ApprovalHandler | None:
        return self._handlers.get(entity_type)


APPROVAL_HANDLERS = ApprovalHandlerRegistry()
