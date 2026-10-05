# SPDX-License-Identifier: AGPL-3.0-only
"""Webhook endpoints and deliveries (/v1/webhooks)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status

from pickwise.platform import audit
from pickwise.platform.auth.dependencies import DB, Authorized, KekDep, SettingsDep, require
from pickwise.platform.crypto import load_tenant_keyring
from pickwise.platform.events import kick_relay
from pickwise.platform.idempotency import idempotent
from pickwise.platform.webhooks import service
from pickwise.platform.webhooks.schemas import (
    DeliveryOut,
    DeliveryPage,
    DeliveryStatus,
    EndpointCreate,
    EndpointOut,
    EndpointUpdate,
    EndpointWithSecret,
    SecretOut,
)
from pickwise.platform.webhooks.ssrf import Resolver, system_resolver

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

Manage = Annotated[Authorized, Depends(require("platform.webhooks.manage"))]


def get_resolver(request: Request) -> Resolver:
    """The DNS resolver for target checks; tests install a fake on ``app.state``."""
    resolver: Resolver = getattr(request.app.state, "webhook_resolver", system_resolver)
    return resolver


ResolverDep = Annotated[Resolver, Depends(get_resolver)]


def _out(e: service.Endpoint) -> EndpointOut:
    return EndpointOut(
        id=e.id,
        url=e.url,
        event_types=e.event_types,
        is_active=e.is_active,
        created_at=e.created_at,
        row_version=e.row_version,
    )


@router.post("/endpoints", response_model=EndpointWithSecret, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    body: EndpointCreate,
    request: Request,
    response: Response,
    db: DB,
    settings: SettingsDep,
    kek: KekDep,
    resolver: ResolverDep,
    auth: Manage,
) -> Any:
    """Register a receiver. The signing secret is in this response only."""
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    keyring = await load_tenant_keyring(db, kek, auth.tenant_id)
    endpoint, secret = await service.create_endpoint(
        db,
        keyring,
        auth.tenant_id,
        url=body.url,
        event_types=body.event_types,
        allow_private=settings.webhook_allow_private_targets,
        resolver=resolver,
    )
    await audit.record(db, "webhook.endpoint_created", "platform.webhook_endpoints", endpoint.id)
    out = EndpointWithSecret(**_out(endpoint).model_dump(), secret=secret)
    # The stored replay body would hold the secret in clear text: replay without it.
    await idem.finish(
        status.HTTP_201_CREATED, _out(endpoint).model_dump(mode="json") | {"secret": None}
    )
    return out


@router.get("/endpoints", response_model=list[EndpointOut])
async def list_endpoints(db: DB, auth: Manage) -> list[EndpointOut]:
    return [_out(e) for e in await service.list_endpoints(db)]


@router.get("/endpoints/{endpoint_id}", response_model=EndpointOut)
async def get_endpoint(endpoint_id: uuid.UUID, db: DB, auth: Manage) -> EndpointOut:
    return _out(await service.get_endpoint(db, endpoint_id))


@router.put("/endpoints/{endpoint_id}", response_model=EndpointOut)
async def update_endpoint(
    endpoint_id: uuid.UUID,
    body: EndpointUpdate,
    db: DB,
    settings: SettingsDep,
    resolver: ResolverDep,
    auth: Manage,
) -> EndpointOut:
    endpoint = await service.update_endpoint(
        db,
        endpoint_id,
        url=body.url,
        event_types=body.event_types,
        is_active=body.is_active,
        row_version=body.row_version,
        allow_private=settings.webhook_allow_private_targets,
        resolver=resolver,
    )
    await audit.record(db, "webhook.endpoint_updated", "platform.webhook_endpoints", endpoint.id)
    return _out(endpoint)


@router.post("/endpoints/{endpoint_id}/rotate-secret", response_model=SecretOut)
async def rotate_secret(endpoint_id: uuid.UUID, db: DB, kek: KekDep, auth: Manage) -> SecretOut:
    """Issue a new signing secret (shown once). Deliveries signed from now on use it."""
    keyring = await load_tenant_keyring(db, kek, auth.tenant_id)
    secret = await service.rotate_secret(db, keyring, auth.tenant_id, endpoint_id)
    await audit.record(db, "webhook.secret_rotated", "platform.webhook_endpoints", endpoint_id)
    return SecretOut(secret=secret)


@router.get("/deliveries", response_model=DeliveryPage)
async def list_deliveries(
    db: DB,
    auth: Manage,
    endpoint_id: uuid.UUID | None = None,
    delivery_status: Annotated[DeliveryStatus | None, Query(alias="status")] = None,
    event_type: str | None = None,
    before: Annotated[uuid.UUID | None, Query(description="Cursor: next_cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> DeliveryPage:
    items, cursor = await service.list_deliveries(
        db,
        endpoint_id=endpoint_id,
        status=delivery_status,
        event_type=event_type,
        before=before,
        limit=limit,
    )
    return DeliveryPage(items=[DeliveryOut(**i) for i in items], next_cursor=cursor)


@router.get("/deliveries/{delivery_id}", response_model=DeliveryOut)
async def get_delivery(delivery_id: uuid.UUID, db: DB, auth: Manage) -> DeliveryOut:
    return DeliveryOut(**await service.get_delivery(db, delivery_id))


@router.post(
    "/deliveries/{delivery_id}/replay",
    response_model=DeliveryOut,
    status_code=status.HTTP_201_CREATED,
)
async def replay_delivery(
    delivery_id: uuid.UUID, db: DB, background: BackgroundTasks, auth: Manage
) -> DeliveryOut:
    """Send the same event to the same endpoint again, as a new delivery."""
    new_id = await service.replay_delivery(db, delivery_id)
    await audit.record(
        db,
        "webhook.replayed",
        "platform.webhook_deliveries",
        new_id,
        {"replayed_from": str(delivery_id)},
    )
    background.add_task(service.kick_delivery, auth.tenant_id, new_id)
    return DeliveryOut(**await service.get_delivery(db, new_id))


@router.post("/events/{event_id}/retry", status_code=status.HTTP_204_NO_CONTENT)
async def retry_event(
    event_id: uuid.UUID, db: DB, background: BackgroundTasks, auth: Manage
) -> None:
    """Let the relay try an event again after it gave up on a failing in-process handler."""
    await service.retry_event(db, event_id)
    await audit.record(db, "event.retried", "platform.outbox_events", event_id)
    background.add_task(kick_relay)
