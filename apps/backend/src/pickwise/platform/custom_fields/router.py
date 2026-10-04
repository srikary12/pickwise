# SPDX-License-Identifier: AGPL-3.0-only
"""Custom-field definitions (/v1/custom-fields). Payload validation is a service call
that modules make themselves; there is no generic "set custom fields" endpoint."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from pickwise.platform import audit
from pickwise.platform.auth.dependencies import DB, Authorized, require
from pickwise.platform.custom_fields import service
from pickwise.platform.custom_fields.schemas import EntityType, FieldCreate, FieldOut, FieldUpdate

router = APIRouter(prefix="/v1/custom-fields", tags=["custom-fields"])

Read = Annotated[Authorized, Depends(require("platform.custom_fields.read"))]
Manage = Annotated[Authorized, Depends(require("platform.custom_fields.manage"))]
ReadSensitive = "platform.custom_fields.read_sensitive"


def _out(d: service.FieldDefinition) -> FieldOut:
    return FieldOut(
        id=d.id,
        entity_type=d.entity_type,
        key=d.key,
        label=d.label,
        field_type=d.field_type,
        options=d.options,
        required=d.required,
        is_sensitive=d.is_sensitive,
        position=d.position,
        archived_at=d.archived_at,
        row_version=d.row_version,
    )


@router.get("", response_model=list[FieldOut])
async def list_fields(
    db: DB,
    auth: Read,
    entity_type: EntityType | None = None,
    include_archived: Annotated[bool, Query()] = False,
) -> list[FieldOut]:
    """Definitions for building forms. Sensitive fields are listed only for callers who may
    see them, and archived ones only on request."""
    definitions = await service.list_definitions(db, entity_type, include_archived=include_archived)
    sees_sensitive = ReadSensitive in auth.grants
    return [_out(d) for d in definitions if sees_sensitive or not d.is_sensitive]


@router.post("", response_model=FieldOut, status_code=status.HTTP_201_CREATED)
async def create_field(body: FieldCreate, db: DB, auth: Manage) -> FieldOut:
    definition = await service.create_definition(db, **body.model_dump())
    await audit.record(
        db,
        "custom_field.created",
        "platform.custom_field_definitions",
        definition.id,
        {"entity_type": definition.entity_type, "key": definition.key},
    )
    return _out(definition)


@router.put("/{field_id}", response_model=FieldOut)
async def update_field(field_id: uuid.UUID, body: FieldUpdate, db: DB, auth: Manage) -> FieldOut:
    definition = await service.update_definition(db, field_id, **body.model_dump())
    await audit.record(
        db, "custom_field.updated", "platform.custom_field_definitions", definition.id
    )
    return _out(definition)


@router.post("/{field_id}/archive", response_model=FieldOut)
async def archive_field(field_id: uuid.UUID, db: DB, auth: Manage) -> FieldOut:
    """Archive instead of delete: stored values stay, new writes are refused."""
    definition = await service.set_archived(db, field_id, True)
    await audit.record(
        db, "custom_field.archived", "platform.custom_field_definitions", definition.id
    )
    return _out(definition)


@router.post("/{field_id}/unarchive", response_model=FieldOut)
async def unarchive_field(field_id: uuid.UUID, db: DB, auth: Manage) -> FieldOut:
    definition = await service.set_archived(db, field_id, False)
    await audit.record(
        db, "custom_field.unarchived", "platform.custom_field_definitions", definition.id
    )
    return _out(definition)
