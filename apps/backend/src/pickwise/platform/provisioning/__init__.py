# SPDX-License-Identifier: AGPL-3.0-only
"""Tenant provisioning and the per-module defaults registry (ADR 0007)."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

# Dependency order (CLAUDE.md "Module boundaries"); hooks run in this order.
MODULE_ORDER = ("platform", "core", "leave", "attendance", "payroll", "ai", "recruit")

ProvisioningHook = Callable[[AsyncSession, uuid.UUID], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _Hook:
    module: str
    name: str
    run: ProvisioningHook


class ProvisioningHookRegistry:
    """Idempotent per-tenant defaults. Each module registers its hook at import;
    hooks run at tenant creation and again on `pickwise tenant sync-defaults`."""

    def __init__(self) -> None:
        self._hooks: list[_Hook] = []

    def register(self, module: str, name: str, hook: ProvisioningHook) -> None:
        if module not in MODULE_ORDER:
            raise ValueError(f"unknown module {module!r}")
        if any(h.module == module and h.name == name for h in self._hooks):
            return
        self._hooks.append(_Hook(module, name, hook))

    def hooks(self) -> list[_Hook]:
        return sorted(self._hooks, key=lambda h: (MODULE_ORDER.index(h.module), h.name))

    async def run_all(self, session: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
        ran = []
        for hook in self.hooks():
            await hook.run(session, tenant_id)
            ran.append(f"{hook.module}.{hook.name}")
        return ran


PROVISIONING_HOOKS = ProvisioningHookRegistry()
