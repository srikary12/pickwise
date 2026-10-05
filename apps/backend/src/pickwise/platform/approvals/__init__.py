# SPDX-License-Identifier: AGPL-3.0-only
"""Approval workflows: policies, steps, delegation, escalation (ADR 0016).

Modules call ``start_approval`` and register an ``ApprovalHandler`` for their entity
type; the API lets approvers decide and admins manage policies.
"""

from pickwise.platform.approvals.registry import (
    APPROVAL_HANDLERS,
    APPROVER_RESOLVERS,
    ApprovalDecision,
    ApprovalHandler,
    ResolveContext,
)
from pickwise.platform.approvals.service import (
    ApproverUnresolvedError,
    NoApprovalPolicyError,
    Outcome,
    start_approval,
)

__all__ = [
    "APPROVAL_HANDLERS",
    "APPROVER_RESOLVERS",
    "ApprovalDecision",
    "ApprovalHandler",
    "ApproverUnresolvedError",
    "NoApprovalPolicyError",
    "Outcome",
    "ResolveContext",
    "start_approval",
]
