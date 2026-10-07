# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Gender = Literal["female", "male", "non_binary", "undisclosed"]
Phone = Field(min_length=5, max_length=30, pattern=r"^[0-9+()\-\s]{5,30}$")
Email = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
StateCode = Field(default=None, pattern=r"^IN-[A-Z]{2}$")


class Versioned(BaseModel):
    row_version: int = Field(ge=1)


# --- personal --------------------------------------------------------------------------------


class PersonalFields(BaseModel):
    date_of_birth: date | None = None
    gender: Gender | None = None
    marital_status: Literal["single", "married", "divorced", "widowed", "undisclosed"] | None = None
    blood_group: Literal["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"] | None = None
    nationality: str | None = Field(default=None, max_length=60)
    father_or_spouse_name: str | None = Field(default=None, max_length=200)
    is_person_with_disability: bool = False
    personal_email: str | None = Email
    personal_phone: str | None = Field(default=None, max_length=30, pattern=r"^[0-9+()\-\s]{5,30}$")

    @model_validator(mode="after")
    def _born_in_the_past(self) -> "PersonalFields":
        if self.date_of_birth and not date(1900, 1, 1) <= self.date_of_birth <= date.today():
            raise ValueError("date of birth isn't plausible")
        return self


class PersonalUpdate(PersonalFields):
    """The whole personal record; row_version is the employee's, as in `GET /v1/employees/{id}`."""

    row_version: int | None = Field(
        default=None, ge=1, description="Omit on the first save; send what you loaded after."
    )


class PersonalOut(PersonalFields):
    employee_id: uuid.UUID
    row_version: int | None


class SelfContactUpdate(BaseModel):
    """What a person may change about themselves."""

    personal_email: str | None = Email
    personal_phone: str | None = Field(default=None, max_length=30, pattern=r"^[0-9+()\-\s]{5,30}$")


# --- addresses ---------------------------------------------------------------------------------


class AddressFields(BaseModel):
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state_code: str | None = StateCode
    pincode: str | None = Field(default=None, pattern=r"^[1-9][0-9]{5}$")
    country_code: str = Field(default="IN", pattern=r"^[A-Z]{2}$")


class AddressSet(AddressFields):
    """A new address of this type from ``valid_from`` (today by default); the one before it ends
    the day before."""

    valid_from: date | None = None


class AddressOut(AddressFields):
    id: uuid.UUID
    address_type: Literal["current", "permanent"]
    valid_from: date
    valid_to: date | None
    row_version: int


# --- emergency contacts, dependents, nominations -----------------------------------------------


class ContactFields(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    relationship: str = Field(min_length=1, max_length=60)
    phone: str = Phone
    email: str | None = Email
    is_primary: bool = False


class ContactCreate(ContactFields):
    pass


class ContactUpdate(ContactFields, Versioned):
    pass


class ContactOut(ContactFields):
    id: uuid.UUID
    row_version: int


Relationship = Literal["spouse", "child", "father", "mother", "sibling", "other"]


class DependentFields(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    relationship: Relationship
    date_of_birth: date | None = None
    gender: Gender | None = None


class DependentCreate(DependentFields):
    pass


class DependentUpdate(DependentFields, Versioned):
    pass


class DependentOut(DependentFields):
    id: uuid.UUID
    row_version: int


Scheme = Literal["pf", "eps", "edli", "gratuity", "insurance"]


class NominationShare(BaseModel):
    dependent_id: uuid.UUID
    share_percent: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)


class NominationsSet(BaseModel):
    """Replace the nominees of one scheme. The shares must total exactly 100; send an empty list
    to remove the scheme's nominations."""

    shares: list[NominationShare] = Field(max_length=20)

    @model_validator(mode="after")
    def _total(self) -> "NominationsSet":
        if self.shares and sum(s.share_percent for s in self.shares) != 100:
            raise ValueError("the shares must total 100")
        if len({s.dependent_id for s in self.shares}) != len(self.shares):
            raise ValueError("a person can be nominated once per scheme")
        return self


class NominationOut(BaseModel):
    scheme: Scheme
    dependent_id: uuid.UUID
    share_percent: Decimal


# --- education, experience, documents ----------------------------------------------------------


class EducationFields(BaseModel):
    institution: str = Field(min_length=1, max_length=200)
    degree: str = Field(min_length=1, max_length=200)
    field_of_study: str | None = Field(default=None, max_length=200)
    start_year: int | None = Field(default=None, ge=1950, le=2100)
    end_year: int | None = Field(default=None, ge=1950, le=2100)

    @model_validator(mode="after")
    def _order(self) -> "EducationFields":
        if self.start_year and self.end_year and self.end_year < self.start_year:
            raise ValueError("end_year can't be before start_year")
        return self


class EducationCreate(EducationFields):
    pass


class EducationUpdate(EducationFields, Versioned):
    pass


class EducationOut(EducationFields):
    id: uuid.UUID
    row_version: int


class ExperienceFields(BaseModel):
    employer: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    from_date: date | None = None
    to_date: date | None = None

    @model_validator(mode="after")
    def _order(self) -> "ExperienceFields":
        if self.from_date and self.to_date and self.to_date < self.from_date:
            raise ValueError("to_date can't be before from_date")
        return self


class ExperienceCreate(ExperienceFields):
    pass


class ExperienceUpdate(ExperienceFields, Versioned):
    pass


class ExperienceOut(ExperienceFields):
    id: uuid.UUID
    row_version: int


DocumentCategory = Literal[
    "offer_letter",
    "appointment_letter",
    "id_proof",
    "address_proof",
    "education",
    "experience",
    "policy_ack",
    "other",
]


class DocumentCreate(BaseModel):
    category: DocumentCategory
    file_id: uuid.UUID = Field(
        description="A file uploaded with owner_entity_type employee_document."
    )
    title: str = Field(min_length=1, max_length=200)
    visible_to_employee: bool = False
    expires_on: date | None = None


class DocumentOut(BaseModel):
    id: uuid.UUID
    category: DocumentCategory
    file_id: uuid.UUID
    title: str
    visible_to_employee: bool
    expires_on: date | None
    row_version: int
