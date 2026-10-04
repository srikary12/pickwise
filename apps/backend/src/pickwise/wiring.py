# SPDX-License-Identifier: AGPL-3.0-only
"""Imports every module's registrations (permissions, provisioning hooks, scope
resolvers, …) so the API, worker and CLI see the same complete registries.

A composition-root helper: only api/, worker/ and cli/ import it.
"""

import importlib

_REGISTRATIONS = (
    "pickwise.platform.permissions",
    "pickwise.platform.scopes",
    "pickwise.platform.provisioning.platform_defaults",
)


def register_all() -> None:
    for module in _REGISTRATIONS:
        importlib.import_module(module)
