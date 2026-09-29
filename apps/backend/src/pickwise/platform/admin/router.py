# SPDX-License-Identifier: AGPL-3.0-only
"""Tenant administration endpoints (/v1/admin/…, /v1/permissions).

Every route declares its permission with ``require``; list queries apply the
caller's data scopes; updates use optimistic concurrency (row_version → 409).
"""

import uuid
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from sqlalchemy import bindparam, column, select, table, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform import audit
from pickwise.platform.admin.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    InviteRequest,
    MemberOut,
    MembershipUpdate,
    PermissionOut,
    RoleAssignmentCreate,
    RoleAssignmentOut,
    RoleCreate,
    RoleOut,
    RolePermissionsUpdate,
    SsoConfigOut,
    SsoConfigWrite,
    TenantSettingsOut,
    TenantSettingsUpdate,
)
from pickwise.platform.auth import sso
from pickwise.platform.auth.api_keys import create_api_key, revoke_api_key
from pickwise.platform.auth.dependencies import (
    DB,
    Authorized,
    KekDep,
    SettingsDep,
    require,
    signed_in,
)
from pickwise.platform.auth.service import AuthStage
from pickwise.platform.crypto import load_tenant_keyring
from pickwise.platform.idempotency import idempotent
from pickwise.platform.notifications.email import dispatch
from pickwise.platform.permissions import PERMISSIONS
from pickwise.platform.provisioning.service import add_member, create_invite, ensure_user
from pickwise.platform.scopes import ENTITY_SCOPES, ScopeTarget, scope_filter
from pickwise.shared.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError

router = APIRouter(prefix="/v1", tags=["admin"])

UsersRead = Annotated[Authorized, Depends(require("platform.users.read"))]
UsersInvite = Annotated[Authorized, Depends(require("platform.users.invite"))]
UsersManage = Annotated[Authorized, Depends(require("platform.users.manage"))]
RolesRead = Annotated[Authorized, Depends(require("platform.roles.read"))]
RolesManage = Annotated[Authorized, Depends(require("platform.roles.manage"))]
KeysManage = Annotated[Authorized, Depends(require("platform.api_keys.manage"))]
SsoManage = Annotated[Authorized, Depends(require("platform.sso.manage"))]
TenantManage = Annotated[Authorized, Depends(require("platform.tenant.manage"))]

_users = table(
    "users",
    column("id"),
    column("email"),
    column("display_name"),
    column("mfa_enabled"),
    schema="platform",
)
_memberships = table(
    "memberships",
    column("id"),
    column("user_id"),
    column("status"),
    column("row_version"),
    schema="platform",
)


def _stale() -> ConflictError:
    return ConflictError(
        "Someone else changed this since you loaded it. Reload and try again.",
        code="stale_row_version",
    )


# --- permission catalog -----------------------------------------------------


@router.get(
    "/permissions",
    response_model=list[PermissionOut],
    dependencies=[Depends(signed_in(AuthStage.READY))],
)
async def list_permissions() -> list[PermissionOut]:
    return [
        PermissionOut(
            code=p.code, module=p.module, description=p.description, is_sensitive=p.is_sensitive
        )
        for p in PERMISSIONS.all()
    ]


# --- users ------------------------------------------------------------------


async def _members(
    db: AsyncSession, auth: Authorized, membership_id: uuid.UUID | None = None
) -> list[MemberOut]:
    visible = scope_filter(auth.scopes, auth.principal, ScopeTarget(user_column=_users.c.id))
    query = (
        select(
            _memberships.c.id,
            _users.c.id,
            _users.c.email,
            _users.c.display_name,
            _memberships.c.status,
            _users.c.mfa_enabled,
            _memberships.c.row_version,
        )
        .select_from(_memberships.join(_users, _users.c.id == _memberships.c.user_id))
        .where(visible, _memberships.c.status != "removed")
        .order_by(_users.c.display_name)
    )
    if membership_id is not None:
        query = query.where(_memberships.c.id == membership_id)
    rows = (await db.execute(query)).all()
    assignments = (
        await db.execute(
            text(
                "SELECT ra.membership_id, ra.id, ra.role_id, r.key, ra.scope_type, ra.scope_id "
                "FROM platform.role_assignments ra JOIN platform.roles r "
                "  ON r.tenant_id = ra.tenant_id AND r.id = ra.role_id "
                "WHERE ra.valid_during IS NULL OR ra.valid_during @> now() ORDER BY r.key"
            )
        )
    ).all()
    by_member: dict[uuid.UUID, list[RoleAssignmentOut]] = {}
    for a in assignments:
        by_member.setdefault(a[0], []).append(
            RoleAssignmentOut(id=a[1], role_id=a[2], role_key=a[3], scope_type=a[4], scope_id=a[5])
        )
    return [
        MemberOut(
            membership_id=r[0],
            user_id=r[1],
            email=r[2],
            display_name=r[3],
            status=r[4],
            mfa_enabled=r[5],
            row_version=r[6],
            roles=by_member.get(r[0], []),
        )
        for r in rows
    ]


@router.get("/admin/users", response_model=list[MemberOut])
async def list_users(db: DB, auth: UsersRead) -> list[MemberOut]:
    return await _members(db, auth)


@router.post("/admin/users/invite", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: InviteRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: DB,
    settings: SettingsDep,
    kek: KekDep,
    auth: UsersInvite,
) -> Any:
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    role: uuid.UUID | None = (
        await db.execute(
            text("SELECT id FROM platform.roles WHERE key = :k AND archived_at IS NULL"),
            {"k": body.role_key},
        )
    ).scalar_one_or_none()
    if role is None:
        raise UnprocessableError("Unknown role.", code="unknown_role")
    _check_can_grant(auth, await _role_permissions(db, role))
    user_id = await ensure_user(db, body.email, body.display_name)
    existing: str | None = (
        await db.execute(
            text("SELECT status FROM platform.memberships WHERE user_id = :u"), {"u": user_id}
        )
    ).scalar_one_or_none()
    if existing in ("active", "suspended"):
        raise ConflictError("That person is already a member.", code="already_member")
    if existing == "removed":
        await db.execute(
            text("UPDATE platform.memberships SET status = 'invited' WHERE user_id = :u"),
            {"u": user_id},
        )
    await add_member(db, auth.tenant_id, user_id, body.role_key, invited_by=auth.principal.user_id)
    tenant_name: str = (
        await db.execute(
            text("SELECT name FROM platform.tenants WHERE id = :t"), {"t": auth.tenant_id}
        )
    ).scalar_one()
    queued = await create_invite(
        db,
        settings,
        kek,
        tenant_id=auth.tenant_id,
        user_id=user_id,
        email=body.email,
        tenant_name=tenant_name,
    )
    background.add_task(dispatch, [queued])
    member = next(m for m in await _members(db, auth) if m.user_id == user_id)
    await idem.finish(status.HTTP_201_CREATED, member.model_dump(mode="json"))
    return member


@router.patch("/admin/users/{membership_id}", response_model=MemberOut)
async def update_membership(
    membership_id: uuid.UUID, body: MembershipUpdate, db: DB, auth: UsersManage
) -> MemberOut:
    if membership_id == auth.principal.membership_id:
        raise ForbiddenError("You can't change your own membership.", code="self_change")
    updated = (
        await db.execute(
            text(
                "UPDATE platform.memberships SET status = :s WHERE id = :id AND row_version = :v "
                "RETURNING id"
            ),
            {"s": body.status, "id": membership_id, "v": body.row_version},
        )
    ).first()
    if updated is None:
        await _not_found_or_stale(db, "platform.memberships", membership_id)
    if body.status != "active":
        # Revoke their sessions' access to this tenant right away.
        await db.execute(
            text(
                "UPDATE platform.sessions SET revoked_at = now() WHERE revoked_at IS NULL "
                "AND active_tenant_id = :t AND user_id = (SELECT user_id FROM platform.memberships "
                "WHERE id = :id)"
            ),
            {"t": auth.tenant_id, "id": membership_id},
        )
    members = await _members(db, auth, membership_id)
    return members[0]


async def _not_found_or_stale(db: AsyncSession, table_name: str, row_id: uuid.UUID) -> NoReturn:
    exists = (
        await db.execute(text(f"SELECT 1 FROM {table_name} WHERE id = :id"), {"id": row_id})  # noqa: S608
    ).first()
    raise (_stale() if exists else NotFoundError("Not found."))


# --- roles ------------------------------------------------------------------


async def _role_permissions(db: AsyncSession, role_id: uuid.UUID) -> set[str]:
    rows: list[str] = list(
        (
            await db.execute(
                text("SELECT permission_code FROM platform.role_permissions WHERE role_id = :r"),
                {"r": role_id},
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


def _check_can_grant(auth: Authorized, permissions: set[str]) -> None:
    """Nobody can hand out a permission they don't hold themselves."""
    missing = sorted(permissions - set(auth.grants))
    if missing:
        raise ForbiddenError(
            f"You can't grant permissions you don't hold: {', '.join(missing)}",
            code="permission_denied",
        )


def _validate_codes(codes: list[str]) -> set[str]:
    unknown = sorted(set(codes) - {p.code for p in PERMISSIONS.all()})
    if unknown:
        raise UnprocessableError(
            f"Unknown permissions: {', '.join(unknown)}", code="unknown_permission"
        )
    return set(codes)


async def _roles(db: AsyncSession, role_id: uuid.UUID | None = None) -> list[RoleOut]:
    rows = (
        await db.execute(
            text(
                "SELECT r.id, r.key, r.name, r.description, r.is_system, r.row_version, "
                "  coalesce(array_agg(rp.permission_code ORDER BY rp.permission_code) "
                "           FILTER (WHERE rp.permission_code IS NOT NULL), '{}') "
                "FROM platform.roles r LEFT JOIN platform.role_permissions rp "
                "  ON rp.tenant_id = r.tenant_id AND rp.role_id = r.id "
                "WHERE r.archived_at IS NULL AND (CAST(:id AS uuid) IS NULL OR r.id = :id) "
                "GROUP BY r.id ORDER BY r.is_system DESC, r.name"
            ),
            {"id": role_id},
        )
    ).all()
    return [
        RoleOut(
            id=r[0],
            key=r[1],
            name=r[2],
            description=r[3],
            is_system=r[4],
            row_version=r[5],
            permissions=list(r[6]),
        )
        for r in rows
    ]


@router.get("/admin/roles", response_model=list[RoleOut])
async def list_roles(db: DB, _auth: RolesRead) -> list[RoleOut]:
    return await _roles(db)


@router.post("/admin/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate, request: Request, response: Response, db: DB, auth: RolesManage
) -> Any:
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    codes = _validate_codes(body.permissions)
    _check_can_grant(auth, codes)
    role_id: uuid.UUID | None = (
        await db.execute(
            text(
                "INSERT INTO platform.roles (key, name, description) VALUES (:k, :n, :d) "
                "ON CONFLICT (tenant_id, key) DO NOTHING RETURNING id"
            ),
            {"k": body.key, "n": body.name, "d": body.description},
        )
    ).scalar_one_or_none()
    if role_id is None:
        raise ConflictError("A role with that key already exists.", code="duplicate_key")
    for code in sorted(codes):
        await db.execute(
            text(
                "INSERT INTO platform.role_permissions (role_id, permission_code) VALUES (:r, :c)"
            ),
            {"r": role_id, "c": code},
        )
    role = (await _roles(db, role_id))[0]
    await idem.finish(status.HTTP_201_CREATED, role.model_dump(mode="json"))
    return role


@router.put("/admin/roles/{role_id}/permissions", response_model=RoleOut)
async def set_role_permissions(
    role_id: uuid.UUID, body: RolePermissionsUpdate, db: DB, auth: RolesManage
) -> RoleOut:
    roles = await _roles(db, role_id)
    if not roles:
        raise NotFoundError("Role not found.")
    if roles[0].is_system:
        raise ConflictError(
            "System roles take their permissions from the product; create a custom role instead.",
            code="system_role",
        )
    codes = _validate_codes(body.permissions)
    _check_can_grant(auth, codes)
    bumped = (
        await db.execute(
            text(
                "UPDATE platform.roles SET updated_at = now() WHERE id = :r AND row_version = :v "
                "RETURNING id"
            ),
            {"r": role_id, "v": body.row_version},
        )
    ).first()
    if bumped is None:
        raise _stale()
    await db.execute(
        text(
            "DELETE FROM platform.role_permissions WHERE role_id = :r AND NOT (permission_code = "
            "ANY(:c))"
        ),
        {"r": role_id, "c": sorted(codes)},
    )
    for code in sorted(codes):
        await db.execute(
            text(
                "INSERT INTO platform.role_permissions (role_id, permission_code) VALUES (:r, :c) "
                "ON CONFLICT DO NOTHING"
            ),
            {"r": role_id, "c": code},
        )
    return (await _roles(db, role_id))[0]


@router.post(
    "/admin/role-assignments", response_model=RoleAssignmentOut, status_code=status.HTTP_201_CREATED
)
async def grant_role(
    body: RoleAssignmentCreate, request: Request, response: Response, db: DB, auth: RolesManage
) -> Any:
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    if (body.scope_type in ENTITY_SCOPES) != (body.scope_id is not None):
        raise UnprocessableError(
            "scope_id is required for entity scopes and not allowed otherwise.",
            code="invalid_scope",
        )
    role_key: str | None = (
        await db.execute(
            text("SELECT key FROM platform.roles WHERE id = :r AND archived_at IS NULL"),
            {"r": body.role_id},
        )
    ).scalar_one_or_none()
    if role_key is None:
        raise NotFoundError("Role not found.")
    _check_can_grant(auth, await _role_permissions(db, body.role_id))
    assignment_id: uuid.UUID | None = (
        await db.execute(
            text(
                "INSERT INTO platform.role_assignments (membership_id, role_id, scope_type, "
                "scope_id, granted_by) "
                "SELECT :m, :r, :st, :sid, :by WHERE EXISTS "
                "  (SELECT 1 FROM platform.memberships WHERE id = :m AND status <> 'removed') "
                "RETURNING id"
            ),
            {
                "m": body.membership_id,
                "r": body.role_id,
                "st": body.scope_type.value,
                "sid": body.scope_id,
                "by": auth.principal.user_id,
            },
        )
    ).scalar_one_or_none()
    if assignment_id is None:
        raise NotFoundError("Member not found.")
    await audit.record(
        db,
        "role.grant",
        "platform.role_assignments",
        assignment_id,
        {"role_id": str(body.role_id), "membership_id": str(body.membership_id)},
    )
    out = RoleAssignmentOut(
        id=assignment_id,
        role_id=body.role_id,
        role_key=role_key,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
    )
    await idem.finish(status.HTTP_201_CREATED, out.model_dump(mode="json"))
    return out


@router.delete("/admin/role-assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_role(assignment_id: uuid.UUID, db: DB, auth: RolesManage) -> None:
    row = (
        await db.execute(
            text(
                "SELECT ra.membership_id, r.key FROM platform.role_assignments ra "
                "JOIN platform.roles r ON r.tenant_id = ra.tenant_id AND r.id = ra.role_id WHERE "
                "ra.id = :id"
            ),
            {"id": assignment_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Role assignment not found.")
    if row.key == "tenant_admin":
        remaining: int = (
            await db.execute(
                text(
                    "SELECT count(*) FROM platform.role_assignments ra "
                    "JOIN platform.roles r ON r.tenant_id = ra.tenant_id AND r.id = ra.role_id "
                    "JOIN platform.memberships m ON m.tenant_id = ra.tenant_id AND m.id = "
                    "ra.membership_id "
                    "WHERE r.key = 'tenant_admin' AND m.status = 'active' AND ra.id <> :id"
                ),
                {"id": assignment_id},
            )
        ).scalar_one()
        if not remaining:
            raise ConflictError("A tenant needs at least one active admin.", code="last_admin")
    await db.execute(
        text("DELETE FROM platform.role_assignments WHERE id = :id"), {"id": assignment_id}
    )
    await audit.record(
        db,
        "role.revoke",
        "platform.role_assignments",
        assignment_id,
        {"membership_id": str(row.membership_id)},
    )


# --- API keys ----------------------------------------------------------------------------------


@router.get("/admin/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(db: DB, _auth: KeysManage) -> list[ApiKeyOut]:
    rows = (
        await db.execute(
            text(
                "SELECT id, name, prefix, scopes, expires_at, last_used_at, revoked_at, created_at "
                "FROM platform.api_keys ORDER BY created_at DESC"
            )
        )
    ).all()
    return [
        ApiKeyOut(
            id=r[0],
            name=r[1],
            prefix=r[2],
            scopes=list(r[3]),
            expires_at=r[4],
            last_used_at=r[5],
            revoked_at=r[6],
            created_at=r[7],
        )
        for r in rows
    ]


@router.post("/admin/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: ApiKeyCreate, request: Request, response: Response, db: DB, auth: KeysManage
) -> Any:
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    created = await create_api_key(
        db,
        name=body.name,
        scopes=body.scopes,
        expires_at=body.expires_at,
        grantable=set(auth.grants),
    )
    listed = next(k for k in await list_api_keys(db, auth) if k.id == created.id)
    out = ApiKeyCreated(**listed.model_dump(), key=created.key)
    # The stored replay never contains the key itself.
    await idem.finish(status.HTTP_201_CREATED, {**out.model_dump(mode="json"), "key": None})
    response.headers["Cache-Control"] = "no-store"
    return out


@router.delete("/admin/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: uuid.UUID, db: DB, _auth: KeysManage) -> None:
    await revoke_api_key(db, key_id)


# --- single sign-on ---------------------------------------------------------


async def _sso_configs(db: AsyncSession, config_id: uuid.UUID | None = None) -> list[SsoConfigOut]:
    rows = (
        await db.execute(
            text(
                "SELECT c.id, c.issuer, c.client_id, c.allowed_domains, c.enforce_sso, "
                "c.jit_provisioning, "
                "  r.key, c.row_version FROM platform.tenant_sso_configs c "
                "LEFT JOIN platform.roles r ON r.tenant_id = c.tenant_id AND r.id = "
                "c.default_role_id "
                "WHERE CAST(:id AS uuid) IS NULL OR c.id = :id ORDER BY c.issuer"
            ),
            {"id": config_id},
        )
    ).all()
    return [
        SsoConfigOut(
            id=r[0],
            issuer=r[1],
            client_id=r[2],
            allowed_domains=[str(d) for d in r[3]],
            enforce_sso=r[4],
            jit_provisioning=r[5],
            default_role_key=r[6],
            row_version=r[7],
        )
        for r in rows
    ]


@router.get("/admin/sso-configs", response_model=list[SsoConfigOut])
async def list_sso_configs(db: DB, _auth: SsoManage) -> list[SsoConfigOut]:
    return await _sso_configs(db)


async def _role_id(db: AsyncSession, key: str | None) -> uuid.UUID | None:
    if key is None:
        return None
    role_id: uuid.UUID | None = (
        await db.execute(
            text("SELECT id FROM platform.roles WHERE key = :k AND archived_at IS NULL"), {"k": key}
        )
    ).scalar_one_or_none()
    if role_id is None:
        raise UnprocessableError("Unknown role.", code="unknown_role")
    return role_id


def _domains(values: list[str]) -> list[str]:
    cleaned = sorted({v.strip().lower().lstrip("@") for v in values if v.strip()})
    if not cleaned or any(" " in d or "." not in d for d in cleaned):
        raise UnprocessableError("List domains like acme.com.", code="invalid_domain")
    return cleaned


@router.post("/admin/sso-configs", response_model=SsoConfigOut, status_code=status.HTTP_201_CREATED)
async def create_sso_config(
    body: SsoConfigWrite, request: Request, response: Response, db: DB, kek: KekDep, auth: SsoManage
) -> Any:
    idem = await idempotent(request, db, auth.principal)
    if (replay := await idem.begin()) is not None:
        response.status_code = replay.status_code
        return replay.body
    if not body.client_secret:
        raise UnprocessableError("client_secret is required.", code="missing_secret")
    config_id: uuid.UUID = (await db.execute(text("SELECT uuidv7()"))).scalar_one()
    keyring = await load_tenant_keyring(db, kek, auth.tenant_id)
    inserted = (
        await db.execute(
            text(
                "INSERT INTO platform.tenant_sso_configs (id, issuer, client_id, "
                "client_secret_enc, allowed_domains, "
                "  enforce_sso, jit_provisioning, default_role_id) "
                "VALUES (:id, :iss, :cid, :sec, CAST(:dom AS citext[]), :enf, :jit, :role) "
                "ON CONFLICT (tenant_id, issuer) DO NOTHING RETURNING id"
            ),
            {
                "id": config_id,
                "iss": body.issuer.rstrip("/"),
                "cid": body.client_id,
                "sec": keyring.encrypt(
                    body.client_secret.encode(), sso.client_secret_aad(config_id)
                ),
                "dom": _domains(body.allowed_domains),
                "enf": body.enforce_sso,
                "jit": body.jit_provisioning,
                "role": await _role_id(db, body.default_role_key),
            },
        )
    ).first()
    if inserted is None:
        raise ConflictError("That issuer is already configured.", code="duplicate_issuer")
    out = (await _sso_configs(db, config_id))[0]
    await idem.finish(status.HTTP_201_CREATED, out.model_dump(mode="json"))
    return out


@router.put("/admin/sso-configs/{config_id}", response_model=SsoConfigOut)
async def update_sso_config(
    config_id: uuid.UUID, body: SsoConfigWrite, db: DB, kek: KekDep, auth: SsoManage
) -> SsoConfigOut:
    if body.row_version is None:
        raise UnprocessableError("row_version is required.", code="missing_row_version")
    secret = None
    if body.client_secret:
        keyring = await load_tenant_keyring(db, kek, auth.tenant_id)
        secret = keyring.encrypt(body.client_secret.encode(), sso.client_secret_aad(config_id))
    updated = (
        await db.execute(
            text(
                "UPDATE platform.tenant_sso_configs SET issuer = :iss, client_id = :cid, "
                "  client_secret_enc = coalesce(:sec, client_secret_enc), allowed_domains = "
                "CAST(:dom AS citext[]), "
                "  enforce_sso = :enf, jit_provisioning = :jit, default_role_id = :role "
                "WHERE id = :id AND row_version = :v RETURNING id"
            ),
            {
                "id": config_id,
                "v": body.row_version,
                "iss": body.issuer.rstrip("/"),
                "cid": body.client_id,
                "sec": secret,
                "dom": _domains(body.allowed_domains),
                "enf": body.enforce_sso,
                "jit": body.jit_provisioning,
                "role": await _role_id(db, body.default_role_key),
            },
        )
    ).first()
    if updated is None:
        await _not_found_or_stale(db, "platform.tenant_sso_configs", config_id)
    return (await _sso_configs(db, config_id))[0]


@router.delete("/admin/sso-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sso_config(config_id: uuid.UUID, db: DB, _auth: SsoManage) -> None:
    deleted = (
        await db.execute(
            text("DELETE FROM platform.tenant_sso_configs WHERE id = :id RETURNING id"),
            {"id": config_id},
        )
    ).first()
    if deleted is None:
        raise NotFoundError("SSO configuration not found.")


# --- tenant settings --------------------------------------------------------


async def _tenant(db: AsyncSession, tenant_id: uuid.UUID) -> TenantSettingsOut:
    row = (
        await db.execute(
            text(
                "SELECT id, slug, name, coalesce((settings ->> 'mfa_required')::boolean, false), "
                "row_version "
                "FROM platform.tenants WHERE id = :t"
            ),
            {"t": tenant_id},
        )
    ).one()
    return TenantSettingsOut(
        id=row[0], slug=row[1], name=row[2], mfa_required=row[3], row_version=row[4]
    )


@router.get("/admin/tenant", response_model=TenantSettingsOut)
async def get_tenant_settings(db: DB, auth: TenantManage) -> TenantSettingsOut:
    return await _tenant(db, auth.tenant_id)


@router.patch("/admin/tenant", response_model=TenantSettingsOut)
async def update_tenant_settings(
    body: TenantSettingsUpdate, db: DB, auth: TenantManage
) -> TenantSettingsOut:
    updated = (
        await db.execute(
            text(
                "UPDATE platform.tenants SET settings = settings || :s WHERE id = :t AND "
                "row_version = :v "
                "RETURNING id"
            ).bindparams(bindparam("s", type_=JSONB)),
            {"s": {"mfa_required": body.mfa_required}, "t": auth.tenant_id, "v": body.row_version},
        )
    ).first()
    if updated is None:
        raise _stale()
    await audit.record(
        db,
        "tenant.settings_changed",
        "platform.tenants",
        auth.tenant_id,
        {"mfa_required": body.mfa_required},
    )
    return await _tenant(db, auth.tenant_id)
