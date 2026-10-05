# SPDX-License-Identifier: AGPL-3.0-only
"""Erasing a data subject (DPDP): a registry every module adds to, and the runner (ADR 0018)."""

from pickwise.platform.erasure.registry import (
    ERASURE_HANDLERS,
    ErasureContext,
    ErasureHandler,
    ErasureHandlerRegistry,
    ErasureSubject,
    ScrubTarget,
)
from pickwise.platform.erasure.service import ErasureReport, run_erasure

__all__ = [
    "ERASURE_HANDLERS",
    "ErasureContext",
    "ErasureHandler",
    "ErasureHandlerRegistry",
    "ErasureReport",
    "ErasureSubject",
    "ScrubTarget",
    "run_erasure",
]
