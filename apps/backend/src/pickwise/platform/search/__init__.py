# SPDX-License-Identifier: AGPL-3.0-only
"""Global search: a registry that modules plug providers into (``GET /v1/search``)."""

from pickwise.platform.search.registry import SEARCH_PROVIDERS, SearchHit, SearchProvider

__all__ = ["SEARCH_PROVIDERS", "SearchHit", "SearchProvider"]
