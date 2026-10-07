# SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI dependencies shared by core routers."""

from typing import Annotated

from fastapi import Depends

from pickwise.platform.auth.dependencies import DB, AuthDep, KekDep
from pickwise.platform.crypto import Keyring, load_tenant_keyring
from pickwise.shared.errors import NotAuthenticatedError


async def tenant_keyring(db: DB, kek: KekDep, auth: AuthDep) -> Keyring:
    """The caller's tenant keys, for routes that encrypt, decrypt or blind-index a value."""
    if auth.principal is None or auth.principal.tenant_id is None:
        raise NotAuthenticatedError("Sign in to continue.")
    return await load_tenant_keyring(db, kek, auth.principal.tenant_id)


Keys = Annotated[Keyring, Depends(tenant_keyring)]
