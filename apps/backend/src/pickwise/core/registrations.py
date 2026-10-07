# SPDX-License-Identifier: AGPL-3.0-only
"""What core plugs into platform's registries: employee search, audited reveal of restricted
values, approver resolvers, and who may download employee documents."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.core.crypto_context import keys_and_tenant
from pickwise.core.employees.service import LIVE, like_pattern, visible
from pickwise.core.identity import service as identity
from pickwise.core.tables import current_jobs, departments, designations, employees
from pickwise.platform.approvals.registry import APPROVER_RESOLVERS, ResolveContext
from pickwise.platform.auth.dependencies import Authorized
from pickwise.platform.files.access import FILE_ACCESS
from pickwise.platform.files.service import FileRecord
from pickwise.platform.rbac.principal import Principal
from pickwise.platform.reveal.registry import REVEAL_FIELDS, RevealField
from pickwise.platform.scopes import ScopeTarget, scope_filter
from pickwise.platform.search.registry import SEARCH_PROVIDERS, SearchHit, SearchProvider

# --- search -----------------------------------------------------------------------------------


async def search_employees(
    session: AsyncSession, auth: Authorized, query: str, limit: int
) -> list[SearchHit]:
    pattern = like_pattern(query)
    rows = (
        await session.execute(
            select(
                employees.c.id,
                employees.c.display_name,
                employees.c.employee_code,
                designations.c.name,
                departments.c.name,
            )
            .select_from(
                employees.outerjoin(current_jobs, current_jobs.c.employee_id == employees.c.id)
                .outerjoin(designations, designations.c.id == current_jobs.c.designation_id)
                .outerjoin(departments, departments.c.id == current_jobs.c.department_id)
            )
            .where(
                visible(auth.scopes, auth.principal),
                employees.c.status.in_(LIVE),
                employees.c.display_name.ilike(pattern, escape="\\")
                | employees.c.employee_code.ilike(pattern, escape="\\")
                | employees.c.work_email.ilike(pattern, escape="\\"),
            )
            .order_by(employees.c.display_name)
            .limit(limit)
        )
    ).all()
    return [
        SearchHit(
            kind="employee",
            id=uuid.UUID(str(r[0])),
            title=str(r[1]),
            subtitle=" · ".join(p for p in (str(r[2]), r[3], r[4]) if p),
            link=f"/employees/{r[0]}",
        )
        for r in rows
    ]


SEARCH_PROVIDERS.register(
    SearchProvider(
        kind="employee",
        label="Employees",
        permission="core.directory.read",
        search=search_employees,
    )
)

# --- reveal -----------------------------------------------------------------------------------


async def reveal_identity(db: AsyncSession, auth: Authorized, document_id: uuid.UUID) -> str | None:
    keyring, tenant_id = keys_and_tenant()
    return await identity.reveal_identity(
        db,
        keyring,
        tenant_id,
        document_id,
        lambda column: scope_filter(
            auth.scopes, auth.principal, ScopeTarget(employee_column=column)
        ),
    )


async def reveal_bank(db: AsyncSession, auth: Authorized, account_id: uuid.UUID) -> str | None:
    keyring, tenant_id = keys_and_tenant()
    return await identity.reveal_bank(
        db,
        keyring,
        tenant_id,
        account_id,
        lambda column: scope_filter(
            auth.scopes, auth.principal, ScopeTarget(employee_column=column)
        ),
    )


REVEAL_FIELDS.register(
    RevealField(
        entity_type="identity_document",
        field="value",
        entity="core.identity_documents",
        permission="core.employee.identity.reveal",
        reveal=reveal_identity,
    )
)
REVEAL_FIELDS.register(
    RevealField(
        entity_type="bank_account",
        field="account_number",
        entity="core.bank_accounts",
        permission="core.employee.bank.reveal",
        reveal=reveal_bank,
    )
)

# --- approvers --------------------------------------------------------------------------------


async def _user_of(db: AsyncSession, employee_id: uuid.UUID | None) -> list[uuid.UUID]:
    """The sign-in user behind an employee, if they have one and it's active."""
    if employee_id is None:
        return []
    rows = (
        await db.execute(
            text(
                "SELECT m.user_id FROM core.employees e "
                "JOIN platform.memberships m "
                "  ON m.tenant_id = e.tenant_id AND m.id = e.membership_id "
                "WHERE e.id = :e AND m.status = 'active'"
            ),
            {"e": employee_id},
        )
    ).all()
    return [r[0] for r in rows]


async def _employee_of_user(db: AsyncSession, user_id: uuid.UUID | None) -> uuid.UUID | None:
    if user_id is None:
        return None
    found: uuid.UUID | None = (
        await db.execute(
            text(
                "SELECT e.id FROM core.employees e "
                "JOIN platform.memberships m "
                "  ON m.tenant_id = e.tenant_id AND m.id = e.membership_id "
                "WHERE m.user_id = :u"
            ),
            {"u": user_id},
        )
    ).scalar_one_or_none()
    return found


async def _manager_of(db: AsyncSession, employee_id: uuid.UUID | None) -> uuid.UUID | None:
    if employee_id is None:
        return None
    manager: uuid.UUID | None = (
        await db.execute(
            text(
                "SELECT manager_employee_id FROM core.employee_job_records_current "
                "WHERE employee_id = :e"
            ),
            {"e": employee_id},
        )
    ).scalar_one_or_none()
    return manager


async def _subject(db: AsyncSession, ctx: ResolveContext) -> uuid.UUID | None:
    """The employee a request is about: an `employee_id` attribute, else the requester."""
    raw = ctx.attributes.get("employee_id")
    if isinstance(raw, str):
        try:
            return uuid.UUID(raw)
        except ValueError:
            return None
    return await _employee_of_user(db, ctx.requested_by)


async def resolve_manager(db: AsyncSession, ctx: ResolveContext) -> list[uuid.UUID]:
    return await _user_of(db, await _manager_of(db, await _subject(db, ctx)))


async def resolve_skip_level(db: AsyncSession, ctx: ResolveContext) -> list[uuid.UUID]:
    """The manager's manager of the requester, or, when escalating, of whoever didn't act."""
    start = (
        await _employee_of_user(db, ctx.escalating_from)
        if ctx.escalating_from is not None
        else await _subject(db, ctx)
    )
    first = await _manager_of(db, start)
    return await _user_of(db, await _manager_of(db, first))


async def resolve_dept_head(db: AsyncSession, ctx: ResolveContext) -> list[uuid.UUID]:
    subject = await _subject(db, ctx)
    if subject is None:
        return []
    head: uuid.UUID | None = (
        await db.execute(
            text(
                "SELECT d.head_employee_id FROM core.employee_job_records_current j "
                "JOIN core.departments d ON d.tenant_id = j.tenant_id AND d.id = j.department_id "
                "WHERE j.employee_id = :e"
            ),
            {"e": subject},
        )
    ).scalar_one_or_none()
    return await _user_of(db, head)


APPROVER_RESOLVERS.register("manager", resolve_manager)
APPROVER_RESOLVERS.register("skip_level", resolve_skip_level)
APPROVER_RESOLVERS.register("dept_head", resolve_dept_head)

# --- documents --------------------------------------------------------------------------------

DOCUMENT_OWNER_TYPE = "employee_document"


async def _may_download_document(db: AsyncSession, who: Principal, file: FileRecord) -> bool:
    """HR (who hold employee read) are covered by the document list's own checks; here the
    employee themselves may download what was shared with them."""
    if file.owner_entity_id is None:
        return False
    mine = await _employee_of_user(db, who.user_id)
    if mine != file.owner_entity_id:
        return False
    shared = (
        await db.execute(
            text(
                "SELECT 1 FROM core.employee_documents WHERE file_id = :f "
                "AND employee_id = :e AND visible_to_employee"
            ),
            {"f": file.id, "e": mine},
        )
    ).first()
    return shared is not None


FILE_ACCESS.register(DOCUMENT_OWNER_TYPE, _may_download_document)
