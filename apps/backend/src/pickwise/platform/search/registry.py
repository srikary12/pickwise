# SPDX-License-Identifier: AGPL-3.0-only
"""What can be searched, and who may search it.

Each module that owns searchable records (platform: users; core, Phase 5: employees;
recruit, Phase 10: candidates) registers a provider for its ``kind``. A provider runs only
for callers who hold its permission, applies the caller's data scope itself, and returns
hits that carry nothing beyond what that caller could already see on the record's own page.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.dependencies import Authorized


@dataclass(frozen=True, slots=True)
class SearchHit:
    kind: str
    id: uuid.UUID
    title: str
    subtitle: str | None
    link: str


SearchFunction = Callable[[AsyncSession, Authorized, str, int], Awaitable[list[SearchHit]]]


@dataclass(frozen=True, slots=True)
class SearchProvider:
    kind: str
    label: str
    permission: str
    search: SearchFunction


class SearchProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, SearchProvider] = {}

    def register(self, provider: SearchProvider) -> None:
        existing = self._providers.get(provider.kind)
        if existing is not None and existing != provider:
            raise ValueError(f"search kind {provider.kind} registered twice")
        self._providers[provider.kind] = provider

    def unregister(self, kind: str) -> None:
        self._providers.pop(kind, None)

    def all(self) -> tuple[SearchProvider, ...]:
        return tuple(sorted(self._providers.values(), key=lambda p: p.kind))


SEARCH_PROVIDERS = SearchProviderRegistry()
