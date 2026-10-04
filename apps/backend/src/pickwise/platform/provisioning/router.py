# SPDX-License-Identifier: AGPL-3.0-only
"""Self-serve signup endpoints (/v1/signup). 404 unless SIGNUP_ENABLED (ADR 0007)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from pickwise.platform import jobs, ratelimit
from pickwise.platform.auth.dependencies import DB, ClientDep, KekDep, SettingsDep, SideDep, public
from pickwise.platform.auth.schemas import EmailStr
from pickwise.platform.notifications.email import dispatch
from pickwise.platform.provisioning import signup
from pickwise.shared.errors import NotFoundError
from pickwise.shared.settings import Settings

router = APIRouter(prefix="/v1/signup", tags=["signup"], dependencies=[Depends(public)])


def _enabled(settings: SettingsDep) -> Settings:
    if not settings.signup_enabled:
        raise NotFoundError("Not found.")
    return settings


Enabled = Annotated[Settings, Depends(_enabled)]


class SignupRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"email": "founder@initech.test", "slug": "initech", "organisation_name": "Initech"}
            ]
        }
    )

    email: EmailStr
    slug: str = Field(pattern=r"^[a-z0-9-]{3,40}$")
    organisation_name: str = Field(min_length=1, max_length=200)


class SignupAccepted(BaseModel):
    signup_id: uuid.UUID
    status_url: str


class SignupVerify(BaseModel):
    token: str = Field(min_length=20, max_length=128)


class SignupStatusOut(BaseModel):
    signup_id: uuid.UUID
    status: str
    tenant_slug: str | None = None


@router.post("", response_model=SignupAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_signup(
    body: SignupRequest,
    background: BackgroundTasks,
    db: DB,
    settings: Enabled,
    kek: KekDep,
    client: ClientDep,
    side: SideDep,
) -> SignupAccepted:
    for rule, subject in (
        (ratelimit.SIGNUP_PER_IP, client.ip or "unknown"),
        (ratelimit.SIGNUP_PER_EMAIL, body.email),
    ):
        async with side() as s:
            await ratelimit.hit(s, settings, rule, subject)
    signup_id, queued = await signup.start(
        db, settings, kek, email=body.email, slug=body.slug, name=body.organisation_name
    )
    background.add_task(dispatch, [queued])
    return SignupAccepted(signup_id=signup_id, status_url=f"/api/v1/signup/{signup_id}")


@router.post("/verify", response_model=SignupAccepted, status_code=status.HTTP_202_ACCEPTED)
async def verify_signup(
    body: SignupVerify, background: BackgroundTasks, db: DB, _settings: Enabled
) -> SignupAccepted:
    signup_id = await signup.verify(db, body.token)
    # Deferred after the commit that marked the request queued; the worker provisions.
    background.add_task(jobs.defer, "pickwise.provision_signup", signup_id=str(signup_id))
    return SignupAccepted(signup_id=signup_id, status_url=f"/api/v1/signup/{signup_id}")


@router.get("/{signup_id}", response_model=SignupStatusOut)
async def signup_status(
    signup_id: uuid.UUID, db: DB, settings: Enabled, client: ClientDep, side: SideDep
) -> SignupStatusOut:
    async with side() as s:
        await ratelimit.hit(s, settings, ratelimit.LOOKUP_PER_IP, client.ip or "unknown")
    result = await signup.status(db, signup_id)
    return SignupStatusOut(
        signup_id=result.id, status=result.status, tenant_slug=result.tenant_slug
    )
