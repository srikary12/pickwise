# SPDX-License-Identifier: AGPL-3.0-only
"""The audit trail (/v1/audit). Reading and exporting are themselves audited."""

import csv
import datetime
import io
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from pickwise.platform import audit
from pickwise.platform.audit_api import service
from pickwise.platform.audit_api.schemas import AuditEventOut, AuditPage
from pickwise.platform.audit_api.service import AuditFilter
from pickwise.platform.auth.dependencies import DB, Authorized, get_client, require
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.csvsafe import safe_cell
from pickwise.shared.db import Database
from pickwise.shared.errors import UnprocessableError

router = APIRouter(prefix="/v1/audit", tags=["audit"])

Read = Annotated[Authorized, Depends(require("platform.audit.read"))]
Export = Annotated[Authorized, Depends(require("platform.audit.export"))]
PAGE = 1000


def filters(
    entity_table: Annotated[
        str | None, Query(pattern=r"^[a-z_]+\.[a-z_]+$", description="schema.table")
    ] = None,
    entity_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: Annotated[
        str | None,
        Query(pattern=r"^[a-z_]+(\.[a-z_]+)*(\.\*)?$", description="Exact, or 'prefix.*'"),
    ] = None,
    request_id: Annotated[str | None, Query(max_length=100)] = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
) -> AuditFilter:
    return AuditFilter(entity_table, entity_id, actor_user_id, action, request_id, since, until)


FilterDep = Annotated[AuditFilter, Depends(filters)]


@router.get("/events", response_model=AuditPage)
async def list_events(
    db: DB,
    auth: Read,
    f: FilterDep,
    cursor: Annotated[str | None, Query(description="next_cursor of the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    """Audit events, newest first. Confidential and restricted values are never in the diffs."""
    items, next_cursor = await service.page(db, f, cursor=cursor, limit=limit)
    return AuditPage(items=[AuditEventOut(**i) for i in items], next_cursor=next_cursor)


_HEADER = [
    "occurred_at",
    "actor_user_id",
    "actor_type",
    "action",
    "entity",
    "entity_id",
    "request_id",
    "ip",
    "changes",
]


def _csv_line(values: list[str]) -> str:
    out = io.StringIO(newline="")
    csv.writer(out).writerow([safe_cell(v) for v in values])
    return out.getvalue()


async def _stream(database: Database, ctx: RequestContext, f: AuditFilter) -> AsyncIterator[str]:
    yield _csv_line(_HEADER)
    cursor: str | None = None
    while True:
        async with database.tenant_session(ctx) as session:
            items, cursor = await service.page(session, f, cursor=cursor, limit=PAGE)
        for i in items:
            entity = f"{i['entity_schema']}.{i['entity_table']}" if i["entity_table"] else ""
            yield _csv_line(
                [
                    i["occurred_at"].isoformat(),
                    str(i["actor_user_id"] or ""),
                    i["actor_type"],
                    i["action"],
                    entity,
                    str(i["entity_id"] or ""),
                    i["request_id"] or "",
                    i["ip"] or "",
                    json.dumps(i["changes"], separators=(",", ":")) if i["changes"] else "",
                ]
            )
        if cursor is None:
            return


@router.get("/events/export")
async def export_events(request: Request, db: DB, auth: Export, f: FilterDep) -> StreamingResponse:
    """The same filters as a CSV download (at most 100,000 rows: narrow the dates beyond that).

    The export is recorded in the audit trail before the first byte is sent.
    """
    total = await service.count(db, f)
    if total > service.MAX_EXPORT_ROWS:
        raise UnprocessableError(
            f"That's {total} events; narrow the filters to at most {service.MAX_EXPORT_ROWS}.",
            code="export_too_large",
        )
    client = get_client(request)
    ctx = RequestContext(
        ActorType.USER, auth.tenant_id, auth.principal.user_id, client.request_id, client.ip
    )
    database: Database = request.app.state.database
    # Recorded and committed (its own transaction) before the first byte is sent. The streamed
    # pages use their own sessions too, so nothing depends on this request's transaction.
    async with database.tenant_session(ctx) as session:
        await audit.record(
            session,
            "export",
            "audit.events",
            None,
            {"kind": "audit_events", "rows": total, **f.as_details()},
        )
    return StreamingResponse(
        _stream(database, ctx, f),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="audit-events.csv"',
            "Cache-Control": "no-store",
        },
    )
