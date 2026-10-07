# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

EmploymentType = Literal[
    "full_time", "part_time", "fixed_term", "contract", "intern", "apprentice", "consultant"
]
ChangeReason = Literal[
    "promotion", "transfer", "redesignation", "manager_change", "rehire", "migration"
]
RecordReason = Literal[
    "hire",
    "promotion",
    "transfer",
    "redesignation",
    "manager_change",
    "correction",
    "rehire",
    "migration",
]


class JobFields(BaseModel):
    legal_entity_id: uuid.UUID
    location_id: uuid.UUID
    department_id: uuid.UUID
    designation_id: uuid.UUID
    grade_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None
    manager_employee_id: uuid.UUID | None = None
    employment_type: EmploymentType


class JobRecordOut(JobFields):
    id: uuid.UUID
    employee_id: uuid.UUID
    valid_from: date
    valid_to: date | None = Field(description="Last day it applies; open-ended if empty.")
    change_reason: RecordReason
    notes: str | None
    is_current: bool
    row_version: int


class JobChange(BaseModel):
    """What changes from ``effective_from``. Fields left out keep their value; send null to
    clear an optional one (grade, cost centre, manager)."""

    effective_from: date
    reason: ChangeReason
    notes: str | None = Field(default=None, max_length=1000)
    legal_entity_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    designation_id: uuid.UUID | None = None
    grade_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None
    manager_employee_id: uuid.UUID | None = None
    employment_type: EmploymentType | None = None


class JobCorrection(BaseModel):
    """Fix a mistake in a record. Fields left out are unchanged; null clears an optional one."""

    row_version: int = Field(ge=1)
    start: date | None = Field(default=None, description="Move the day this record begins.")
    notes: str | None = Field(default=None, max_length=1000)
    legal_entity_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    designation_id: uuid.UUID | None = None
    grade_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None
    manager_employee_id: uuid.UUID | None = None
    employment_type: EmploymentType | None = None
