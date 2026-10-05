# SPDX-License-Identifier: AGPL-3.0-only
"""Notification types and wording for approvals. Titles never carry personal data."""

from pickwise.platform.notifications.registry import NOTIFICATION_TYPES, NotificationType

ENTITY_LABELS = {
    "leave_request": "Leave request",
    "regularization": "Attendance regularisation",
    "requisition": "Job requisition",
    "offer": "Offer",
    "payroll_run": "Payroll run",
    "compensation": "Compensation change",
    "separation": "Separation",
}

NOTIFICATION_TYPES.register(
    NotificationType(
        "approval.assigned",
        "Approval needed",
        "Something is waiting for your approval",
    ),
    NotificationType(
        "approval.reminder",
        "Approval reminder",
        "An approval waiting on you is getting close to its deadline",
    ),
    NotificationType(
        "approval.escalated",
        "Approval escalated",
        "An approval was passed to you, or passed on from you, because it wasn't answered in time",
    ),
    NotificationType(
        "approval.decided",
        "Approval decided",
        "A request you made was approved, rejected or withdrawn",
    ),
)


def label(entity_type: str) -> str:
    return ENTITY_LABELS.get(entity_type, "Request")
