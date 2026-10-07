# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DocType = Literal[
    "pan", "aadhaar", "passport", "uan", "esic_ip", "voter_id", "driving_licence", "visa"
]


class IdentityCreate(BaseModel):
    """A new identity document. The number is encrypted at once and never returned: the API shows
    only the last four characters. Adding one replaces the employee's current document of that
    type (the old one stays as history)."""

    doc_type: DocType
    value: str = Field(min_length=4, max_length=40, description="The number as printed.")
    name_as_per_doc: str | None = Field(default=None, max_length=200)
    issued_on: date | None = None
    expires_on: date | None = None
    file_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _dates(self) -> "IdentityCreate":
        if self.issued_on and self.expires_on and self.expires_on < self.issued_on:
            raise ValueError("expires_on can't be before issued_on")
        return self


class IdentityOut(BaseModel):
    id: uuid.UUID
    doc_type: DocType
    last4: str | None
    has_value: bool = Field(
        description="False for Aadhaar kept as its last four digits only, which can't be revealed."
    )
    name_as_per_doc: str | None
    issued_on: date | None
    expires_on: date | None
    is_current: bool
    file_id: uuid.UUID | None
    verification_status: Literal["unverified", "verified", "rejected"]
    verified_at: datetime | None
    row_version: int


class IdentityVerify(BaseModel):
    status: Literal["verified", "rejected"]
    row_version: int = Field(ge=1)


class BankCreate(BaseModel):
    """A bank account. The number is encrypted and masked afterwards. A primary account takes
    over salary from ``valid_from``; the previous primary ends the day before."""

    account_holder_name: str = Field(min_length=1, max_length=200)
    account_number: str = Field(min_length=6, max_length=24, pattern=r"^[0-9]{6,24}$")
    ifsc: str = Field(pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$", examples=["HDFC0001234"])
    bank_name: str = Field(min_length=1, max_length=200)
    account_type: Literal["savings", "current", "salary"] = "savings"
    is_primary: bool = True
    valid_from: date | None = None


class BankOut(BaseModel):
    id: uuid.UUID
    account_holder_name: str
    last4: str
    ifsc: str
    bank_name: str
    account_type: Literal["savings", "current", "salary"]
    is_primary: bool
    valid_from: date
    valid_to: date | None
    verification_status: Literal["unverified", "penny_drop_ok", "failed"]
    row_version: int
