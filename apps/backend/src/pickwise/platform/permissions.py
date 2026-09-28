# SPDX-License-Identifier: AGPL-3.0-only
"""The permission catalog, defined in code and synced to platform.permissions on
every migrate. Phase 2 fills it in; until then it's empty and the sync is a no-op."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Permission:
    code: str
    module: str
    description: str
    is_sensitive: bool = False


CATALOG: tuple[Permission, ...] = ()
