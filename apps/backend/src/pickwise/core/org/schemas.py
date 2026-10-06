# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Code = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$", examples=["BLR-HQ"])
Name = Field(min_length=1, max_length=200)
StateCode = Field(pattern=r"^IN-[A-Z]{2}$", examples=["IN-KA"])


class Versioned(BaseModel):
    """Updates carry the version the editor loaded; a stale one is rejected with 409."""

    row_version: int = Field(ge=1)


# --- legal entities -------------------------------------------------------------------------


class LegalEntityFields(BaseModel):
    name: str = Name
    legal_name: str = Field(min_length=1, max_length=300)
    country_code: str = Field(default="IN", pattern=r"^[A-Z]{2}$")
    pan: str | None = Field(default=None, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    tan: str | None = Field(default=None, pattern=r"^[A-Z]{4}[0-9]{5}[A-Z]$")
    gstin: str | None = Field(
        default=None, pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
    )
    cin: str | None = Field(default=None, pattern=r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")
    registered_address: dict[str, Any] = Field(default_factory=dict)
    pf_establishment_code: str | None = Field(default=None, max_length=40)
    esi_employer_code: str | None = Field(default=None, max_length=40)


class LegalEntityCreate(LegalEntityFields):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Acme India",
                    "legal_name": "Acme Technologies India Private Limited",
                    "pan": "AABCA1234F",
                    "tan": "BLRA12345B",
                    "gstin": "29AABCA1234F1Z5",
                    "registered_address": {"line1": "12 MG Road", "city": "Bengaluru"},
                }
            ]
        }
    )


class LegalEntityUpdate(LegalEntityFields, Versioned):
    pass


class LegalEntityOut(LegalEntityFields):
    id: uuid.UUID
    archived_at: datetime | None
    row_version: int


class RegistrationFields(BaseModel):
    registration_type: Literal["PT", "LWF", "SHOPS_ESTABLISHMENT", "FACTORY"]
    state_code: str = StateCode
    registration_no: str = Field(min_length=1, max_length=60)
    valid_from: date
    valid_to: date | None = Field(default=None, description="Last day it applies; open if empty.")

    @model_validator(mode="after")
    def _ordered(self) -> "RegistrationFields":
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to can't be before valid_from")
        return self


class RegistrationCreate(RegistrationFields):
    pass


class RegistrationUpdate(RegistrationFields, Versioned):
    pass


class RegistrationOut(RegistrationFields):
    id: uuid.UUID
    legal_entity_id: uuid.UUID
    row_version: int


# --- locations -------------------------------------------------------------------------------


class LocationFields(BaseModel):
    legal_entity_id: uuid.UUID
    code: str = Code
    name: str = Name
    address: dict[str, Any] = Field(default_factory=dict)
    state_code: str = StateCode
    city: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, pattern=r"^[1-9][0-9]{5}$")
    timezone: str | None = Field(default=None, max_length=64, examples=["Asia/Kolkata"])
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, decimal_places=6)
    geofence_radius_m: int | None = Field(default=None, ge=10, le=100_000)

    @model_validator(mode="after")
    def _geofence_needs_a_point(self) -> "LocationFields":
        if self.geofence_radius_m is not None and (self.latitude is None or self.longitude is None):
            raise ValueError("a geofence needs latitude and longitude")
        return self


class LocationCreate(LocationFields):
    pass


class LocationUpdate(LocationFields, Versioned):
    pass


class LocationOut(LocationFields):
    id: uuid.UUID
    archived_at: datetime | None
    row_version: int


# --- cost centres, designations, grades ------------------------------------------------------


class CostCenterFields(BaseModel):
    legal_entity_id: uuid.UUID
    code: str = Code
    name: str = Name


class CostCenterCreate(CostCenterFields):
    pass


class CostCenterUpdate(CostCenterFields, Versioned):
    pass


class CostCenterOut(CostCenterFields):
    id: uuid.UUID
    archived_at: datetime | None
    row_version: int


class DesignationFields(BaseModel):
    code: str = Code
    name: str = Name
    job_family: str | None = Field(default=None, max_length=100)


class DesignationCreate(DesignationFields):
    pass


class DesignationUpdate(DesignationFields, Versioned):
    pass


class DesignationOut(DesignationFields):
    id: uuid.UUID
    archived_at: datetime | None
    row_version: int


class GradeFields(BaseModel):
    code: str = Code
    name: str = Name
    rank: int = Field(ge=0, le=10_000, description="Higher is more senior.")
    ctc_min: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    ctc_max: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)

    @model_validator(mode="after")
    def _range(self) -> "GradeFields":
        if self.ctc_min is not None and self.ctc_max is not None and self.ctc_min > self.ctc_max:
            raise ValueError("ctc_min can't exceed ctc_max")
        return self


class GradeCreate(GradeFields):
    pass


class GradeUpdate(GradeFields, Versioned):
    pass


class GradeOut(GradeFields):
    id: uuid.UUID
    archived_at: datetime | None
    row_version: int


# --- departments -----------------------------------------------------------------------------


class DepartmentFields(BaseModel):
    code: str = Code
    name: str = Name
    cost_center_id: uuid.UUID | None = None


class DepartmentCreate(DepartmentFields):
    parent_id: uuid.UUID | None = None


class DepartmentUpdate(DepartmentFields, Versioned):
    """Moving a department is a separate call; its place in the tree isn't edited here."""


class DepartmentMove(Versioned):
    parent_id: uuid.UUID | None = Field(description="The new parent, or null for the top level.")


class DepartmentOut(DepartmentFields):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    path: str = Field(description="Ancestor ids joined by '.', without hyphens (ltree).")
    depth: int = Field(description="0 for a top-level department.")
    archived_at: datetime | None
    row_version: int
