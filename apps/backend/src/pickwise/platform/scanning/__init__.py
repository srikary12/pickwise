# SPDX-License-Identifier: AGPL-3.0-only
"""Virus scanning. Phase 3's file pipeline scans every upload through ``build_scanner``."""

from collections.abc import AsyncIterator

from pickwise.platform.scanning.base import (
    Scanner,
    ScannerError,
    ScanResult,
    ScanVerdict,
    eicar_bytes,
)
from pickwise.platform.scanning.clamav import ClamAVScanner
from pickwise.platform.scanning.stub import StubScanner
from pickwise.shared.settings import ScannerKind, Settings

__all__ = [
    "ClamAVScanner",
    "ScanResult",
    "ScanVerdict",
    "Scanner",
    "ScannerError",
    "StubScanner",
    "build_scanner",
    "eicar_bytes",
    "iter_bytes",
]


def build_scanner(settings: Settings, kind: ScannerKind | None = None) -> Scanner:
    match kind or settings.scanner:
        case ScannerKind.STUB:
            return StubScanner()
        case ScannerKind.CLAMAV:
            return ClamAVScanner(
                settings.clamav_host, settings.clamav_port, settings.clamav_timeout_seconds
            )


async def iter_bytes(data: bytes, chunk_size: int = 8192) -> AsyncIterator[bytes]:
    for start in range(0, len(data), chunk_size):
        yield data[start : start + chunk_size]
