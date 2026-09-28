# SPDX-License-Identifier: AGPL-3.0-only
"""Keep monthly partitions ahead of time (ADR 0003).

There is no DEFAULT partition, so an insert into a month without a partition
fails loudly. The worker runs this daily; ``pickwise db ensure-partitions`` runs
it by hand.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MONTHS_AHEAD = 3
MONTHS_BACK = 1

_PARTITIONED_PARENTS = text(
    "SELECT c.oid::regclass::text FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind = 'p' AND NOT c.relispartition "
    "AND n.nspname IN ('platform', 'audit', 'core', 'leave', 'attendance', 'payroll', "
    "'ai', 'recruit') "
    "ORDER BY 1"
)

_ENSURE = text(
    "SELECT platform.ensure_monthly_partitions(CAST(:parent AS regclass), :ahead, :back)"
)


async def ensure_partitions(session: AsyncSession) -> dict[str, int]:
    """Create any missing partitions for every partitioned table; returns counts created.

    Needs an ops session: the SECURITY DEFINER function is executable by pickwise_ops only.
    """
    created: dict[str, int] = {}
    for parent in (await session.scalars(_PARTITIONED_PARENTS)).all():
        count = await session.scalar(
            _ENSURE,
            {"parent": parent, "ahead": MONTHS_AHEAD, "back": MONTHS_BACK},
        )
        created[parent] = int(count or 0)
    return created
