# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from pickwise.core.job_records.schemas import JobFields

EmployeeStatus = Literal[
    "draft", "pre_boarding", "active", "notice_period", "leave_of_absence", "exited"
]
# What `status` endpoint can do; the others come from separations and leave (later phases).
SettableStatus = Literal["pre_boarding", "active"]


class JobSummary(BaseModel):
    """The job in force today, with names for display."""

    legal_entity_id: uuid.UUID
    location_id: uuid.UUID
    location_name: str
    department_id: uuid.UUID
    department_name: str
    designation_id: uuid.UUID
    designation_name: str
    manager_employee_id: uuid.UUID | None
    manager_name: str | None
    employment_type: str


class EmployeeOut(BaseModel):
    id: uuid.UUID
    employee_code: str
    status: EmployeeStatus
    display_name: str
    first_name: str
    middle_name: str | None
    last_name: str | None
    preferred_name: str | None
    work_email: str | None
    work_phone: str | None
    date_of_joining: date
    original_hire_date: date | None
    probation_end_date: date | None
    confirmation_date: date | None
    date_of_exit: date | None
    photo_file_id: uuid.UUID | None
    has_login: bool = Field(description="Linked to a person who can sign in.")
    job: JobSummary | None
    custom_fields: dict[str, Any]
    row_version: int


class EmployeePage(BaseModel):
    items: list[EmployeeOut]
    next_cursor: str | None = None


class EmployeeFields(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    preferred_name: str | None = Field(default=None, max_length=100)
    work_email: str | None = Field(
        default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    work_phone: str | None = Field(default=None, max_length=30)
    original_hire_date: date | None = None
    probation_end_date: date | None = None
    confirmation_date: date | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class EmployeeCreate(EmployeeFields):
    date_of_joining: date
    job: JobFields

    @model_validator(mode="after")
    def _dates(self) -> "EmployeeCreate":
        if self.probation_end_date and self.probation_end_date < self.date_of_joining:
            raise ValueError("probation can't end before the joining date")
        return self


class EmployeeUpdate(EmployeeFields):
    row_version: int = Field(ge=1)


class StatusChange(BaseModel):
    to: SettableStatus
    row_version: int = Field(ge=1)


class LinkUser(BaseModel):
    membership_id: uuid.UUID | None = Field(
        description="The person's membership, or null to unlink."
    )
    row_version: int = Field(ge=1)


class DirectoryEntry(BaseModel):
    """What colleagues see: work details only."""

    id: uuid.UUID
    employee_code: str
    display_name: str
    work_email: str | None
    work_phone: str | None
    photo_file_id: uuid.UUID | None
    designation_name: str | None
    department_id: uuid.UUID | None
    department_name: str | None
    location_name: str | None
    manager_employee_id: uuid.UUID | None
    manager_name: str | None


class DirectoryPage(BaseModel):
    items: list[DirectoryEntry]
    next_cursor: str | None = None


class OrgChartNode(BaseModel):
    id: uuid.UUID
    display_name: str
    designation_name: str | None
    department_name: str | None
    manager_employee_id: uuid.UUID | None
    report_count: int


class OrgChart(BaseModel):
    nodes: list[OrgChartNode]
