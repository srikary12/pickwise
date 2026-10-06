# SPDX-License-Identifier: AGPL-3.0-only
"""Every member of the tenant may download the tenant logo."""

from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.branding.service import OWNER_TYPE
from pickwise.platform.files.access import FILE_ACCESS
from pickwise.platform.files.service import FileRecord
from pickwise.platform.rbac.principal import Principal


async def _anyone_in_the_tenant(_db: AsyncSession, _who: Principal, _file: FileRecord) -> bool:
    return True  # row-level security already limits the file to the caller's tenant


FILE_ACCESS.register(OWNER_TYPE, _anyone_in_the_tenant)
