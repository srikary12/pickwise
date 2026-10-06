# SPDX-License-Identifier: AGPL-3.0-only
"""Imports every module's registrations (permissions, provisioning hooks, scope
resolvers, …) so the API, worker and CLI see the same complete registries.

A composition-root helper: only api/, worker/ and cli/ import it.
"""

import importlib

_REGISTRATIONS = (
    "pickwise.platform.permissions",
    "pickwise.platform.scopes",
    "pickwise.platform.approvals.resolvers",
    "pickwise.platform.approvals.types",
    "pickwise.platform.erasure.platform_handler",
    "pickwise.platform.imports.sandbox",
    "pickwise.platform.provisioning.platform_defaults",
    "pickwise.platform.search.users",
    "pickwise.platform.branding.access",
)


def register_all() -> None:
    for module in _REGISTRATIONS:
        importlib.import_module(module)
