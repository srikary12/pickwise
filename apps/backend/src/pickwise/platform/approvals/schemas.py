# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pickwise.platform.approvals.conditions import ConditionError, validate
from pickwise.platform.approvals.policy import MAX_STEPS, StepSpec
from pickwise.platform.approvals.registry import ENTITY_TYPES

EntityType = Literal[
    "leave_request",
    "regularization",
    "requisition",
    "offer",
    "payroll_run",
    "compensation",
    "separation",
]
RequestStatus = Literal["pending", "approved", "rejected", "cancelled", "expired"]
TaskStatus = Literal["pending", "approved", "rejected", "skipped", "delegated", "escalated"]
assert set(EntityType.__args__) == set(ENTITY_TYPES)  # type: ignore[attr-defined]


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str | None


class TaskOut(BaseModel):
    id: uuid.UUID
    assignee: PersonOut
    status: TaskStatus
    acted_at: datetime | None
    comment: str | None
    delegated_from: PersonOut | None
    due_at: datetime | None
    created_at: datetime


class StepOut(BaseModel):
    step_no: int
    name: str
    mode: Literal["any", "all"]
    state: Literal["done", "current", "upcoming"]
    tasks: list[TaskOut]


class RequestOut(BaseModel):
    id: uuid.UUID
    entity_type: EntityType
    entity_id: uuid.UUID
    status: RequestStatus
    current_step: int
    policy_name: str
    requested_by: PersonOut | None
    created_at: datetime
    completed_at: datetime | None
    row_version: int
    steps: list[StepOut]
    my_task_id: uuid.UUID | None = Field(
        description="The caller's pending task, if the request is waiting on them."
    )
    can_cancel: bool


class Decision(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"comment": "Approved: coverage is arranged.", "row_version": 3}]
        }
    )

    comment: str | None = Field(default=None, max_length=2000)
    row_version: int = Field(ge=1, description="The request's row_version you were looking at.")


class InboxItem(BaseModel):
    task_id: uuid.UUID
    request_id: uuid.UUID
    step_no: int
    step_name: str | None
    task_status: TaskStatus
    request_status: RequestStatus
    entity_type: EntityType
    entity_id: uuid.UUID
    policy_name: str
    requested_by: uuid.UUID | None
    requester_name: str | None
    delegated_from_user_id: uuid.UUID | None
    due_at: datetime | None
    created_at: datetime


class InboxPage(BaseModel):
    items: list[InboxItem]
    next_cursor: uuid.UUID | None


class RequestedItem(BaseModel):
    request_id: uuid.UUID
    entity_type: EntityType
    entity_id: uuid.UUID
    status: RequestStatus
    current_step: int
    step_count: int
    policy_name: str
    created_at: datetime
    completed_at: datetime | None


class RequestedPage(BaseModel):
    items: list[RequestedItem]
    next_cursor: uuid.UUID | None


def _check_conditions(value: dict[str, Any]) -> dict[str, Any]:
    try:
        validate(value)
    except ConditionError as exc:
        raise ValueError(str(exc)) from exc
    return value


class PolicyBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    priority: int = Field(
        default=0, ge=-1000, le=1000, description="The highest-priority matching policy wins."
    )
    conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="Which requests this applies to. {} matches everything. "
        "See the condition language in the docs.",
    )
    steps: list[StepSpec] = Field(min_length=1, max_length=MAX_STEPS)
    is_active: bool = True

    _check_conditions = field_validator("conditions")(_check_conditions)


class PolicyCreate(PolicyBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "key": "leave_long",
                    "entity_type": "leave_request",
                    "name": "Leave of 5 days or more",
                    "priority": 10,
                    "conditions": {"field": "days", "op": "gte", "value": 5},
                    "steps": [
                        {"name": "Manager", "approvers": ["manager"], "escalate_after_hours": 48},
                        {"name": "HR", "approvers": ["role:hr_admin"], "mode": "any"},
                    ],
                }
            ]
        }
    )

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    entity_type: EntityType


class PolicyUpdate(PolicyBase):
    row_version: int = Field(ge=1)


class PolicyOut(BaseModel):
    id: uuid.UUID
    key: str
    entity_type: EntityType
    name: str
    priority: int
    conditions: dict[str, Any]
    steps: list[StepSpec]
    is_active: bool
    archived_at: datetime | None
    row_version: int


class DelegationCreate(BaseModel):
    from_user_id: uuid.UUID | None = Field(
        default=None,
        description="Whose approvals are delegated. Defaults to you; setting someone else "
        "needs platform.approvals.manage.",
    )
    to_user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    entity_types: list[EntityType] = Field(
        default_factory=list, description="Request types covered. Empty means all."
    )


class DelegationOut(BaseModel):
    id: uuid.UUID
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    entity_types: list[str]
