# SPDX-License-Identifier: AGPL-3.0-only
"""The reporting hierarchy as a closure table (``core.employee_hierarchy``, ADR 0022).

One row per (ancestor, descendant) pair of the *current* reporting tree, the employee
themselves at depth 0. It is kept exact in the same transaction as every change to who
reports to whom (so a manager change takes effect for scopes and approvals at once), and
``rebuild`` recomputes it from the job records: the daily job calls it to apply future-dated
changes and to heal any drift.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.shared.errors import UnprocessableError

# The longest reporting chain the rebuild follows; a real chain is far shorter, and the bound
# stops a cycle that slipped in from looping.
MAX_DEPTH = 100


async def add_employee(db: AsyncSession, employee_id: uuid.UUID) -> None:
    await db.execute(
        text(
            "INSERT INTO core.employee_hierarchy "
            "(ancestor_employee_id, descendant_employee_id, depth) VALUES (:e, :e, 0) "
            "ON CONFLICT DO NOTHING"
        ),
        {"e": employee_id},
    )


async def in_subtree(db: AsyncSession, root: uuid.UUID, candidate: uuid.UUID) -> bool:
    """Is ``candidate`` the root or anyone below them?"""
    found = (
        await db.execute(
            text(
                "SELECT 1 FROM core.employee_hierarchy "
                "WHERE ancestor_employee_id = :r AND descendant_employee_id = :c"
            ),
            {"r": root, "c": candidate},
        )
    ).first()
    return found is not None


async def ensure_no_cycle(db: AsyncSession, employee_id: uuid.UUID, manager_id: uuid.UUID) -> None:
    """Refuse a manager who reports, directly or not, to the employee."""
    if await in_subtree(db, employee_id, manager_id):
        raise UnprocessableError(
            "That would make someone their own manager further up the chain.",
            code="reporting_cycle",
        )


async def move_subtree(
    db: AsyncSession, employee_id: uuid.UUID, new_manager_id: uuid.UUID | None
) -> None:
    """Re-hang an employee, with everyone who reports to them, under a new manager."""
    params = {"e": employee_id, "m": new_manager_id}
    # Cut the subtree off from its old ancestors (but keep its own internal pairs)…
    await db.execute(
        text(
            "DELETE FROM core.employee_hierarchy h "
            "WHERE h.descendant_employee_id IN ("
            "        SELECT descendant_employee_id FROM core.employee_hierarchy "
            "        WHERE ancestor_employee_id = :e) "
            "  AND h.ancestor_employee_id IN ("
            "        SELECT ancestor_employee_id FROM core.employee_hierarchy "
            "        WHERE descendant_employee_id = :e AND ancestor_employee_id <> :e)"
        ),
        params,
    )
    if new_manager_id is None:
        return
    # …and graft it under the new manager and the manager's ancestors.
    await db.execute(
        text(
            "INSERT INTO core.employee_hierarchy "
            "(ancestor_employee_id, descendant_employee_id, depth) "
            "SELECT a.ancestor_employee_id, d.descendant_employee_id, a.depth + d.depth + 1 "
            "FROM core.employee_hierarchy a CROSS JOIN core.employee_hierarchy d "
            "WHERE a.descendant_employee_id = :m AND d.ancestor_employee_id = :e "
            "ON CONFLICT DO NOTHING"
        ),
        params,
    )


async def rebuild(db: AsyncSession) -> int:
    """Recompute the whole closure for the current tenant from the current job records."""
    await db.execute(text("DELETE FROM core.employee_hierarchy"))
    result = await db.execute(
        text(
            "WITH RECURSIVE edges AS ("
            "  SELECT employee_id AS child, manager_employee_id AS parent "
            "  FROM core.employee_job_records_current WHERE manager_employee_id IS NOT NULL), "
            "closure(anc, dsc, depth) AS ("
            "  SELECT id, id, 0 FROM core.employees "
            "  UNION ALL "
            "  SELECT e.parent, c.dsc, c.depth + 1 FROM closure c "
            "  JOIN edges e ON e.child = c.anc WHERE c.depth < :max) "
            "INSERT INTO core.employee_hierarchy "
            "(ancestor_employee_id, descendant_employee_id, depth) "
            "SELECT anc, dsc, min(depth) FROM closure "
            "WHERE anc <> dsc OR depth = 0 GROUP BY anc, dsc"
        ),
        {"max": MAX_DEPTH},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def direct_report_count(db: AsyncSession, manager_id: uuid.UUID) -> int:
    count: int = (
        await db.execute(
            text(
                "SELECT count(*) FROM core.employee_hierarchy "
                "WHERE ancestor_employee_id = :m AND depth = 1"
            ),
            {"m": manager_id},
        )
    ).scalar_one()
    return count


async def sync_manager_roles(db: AsyncSession) -> None:
    """Give the ``manager`` system role (all reports) to everyone who has reports, and take back
    only the grants made here (``granted_by`` empty) from people who no longer do. A grant an
    administrator made by hand is left alone."""
    await db.execute(
        text(
            "INSERT INTO platform.role_assignments (tenant_id, membership_id, role_id, scope_type) "
            "SELECT platform.current_tenant_id(), e.membership_id, r.id, 'all_reports' "
            "FROM core.employees e "
            "JOIN platform.roles r ON r.tenant_id = e.tenant_id AND r.key = 'manager' "
            "JOIN platform.memberships m ON m.tenant_id = e.tenant_id AND m.id = e.membership_id "
            "WHERE e.membership_id IS NOT NULL AND m.status IN ('active', 'invited') "
            "  AND EXISTS (SELECT 1 FROM core.employee_hierarchy h "
            "              WHERE h.ancestor_employee_id = e.id AND h.depth = 1) "
            "  AND NOT EXISTS (SELECT 1 FROM platform.role_assignments ra "
            "                  WHERE ra.tenant_id = e.tenant_id "
            "                    AND ra.membership_id = e.membership_id "
            "                    AND ra.role_id = r.id AND ra.scope_type = 'all_reports')"
        )
    )
    await db.execute(
        text(
            "DELETE FROM platform.role_assignments ra USING platform.roles r, core.employees e "
            "WHERE r.tenant_id = ra.tenant_id AND r.id = ra.role_id AND r.key = 'manager' "
            "  AND ra.scope_type = 'all_reports' AND ra.granted_by IS NULL "
            "  AND e.tenant_id = ra.tenant_id AND e.membership_id = ra.membership_id "
            "  AND NOT EXISTS (SELECT 1 FROM core.employee_hierarchy h "
            "                  WHERE h.ancestor_employee_id = e.id AND h.depth = 1)"
        )
    )
