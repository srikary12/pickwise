# SPDX-License-Identifier: AGPL-3.0-only
"""Development-only stub scanner.

It flags the EICAR test string and marks everything else clean, logging a loud
warning on every scan. The app refuses to start with it in production.
"""

from collections.abc import AsyncIterator

from pickwise.platform.scanning.base import ScanResult, ScanVerdict, eicar_bytes
from pickwise.shared.logging import get_logger

STUB_WARNING = "STUB SCANNER — DEV ONLY: files are NOT virus-scanned"
EICAR_SIGNATURE = "Eicar-Test-Signature (stub)"

log = get_logger(__name__)


class StubScanner:
    name = "stub"

    async def scan(self, chunks: AsyncIterator[bytes]) -> ScanResult:
        log.warning(STUB_WARNING)
        marker = eicar_bytes()
        # Keep a tail across chunk boundaries so a split marker is still found.
        tail = b""
        async for chunk in chunks:
            window = tail + chunk
            if marker in window:
                return ScanResult(ScanVerdict.INFECTED, EICAR_SIGNATURE)
            tail = window[-(len(marker) - 1) :]
        return ScanResult(ScanVerdict.CLEAN)

    async def ping(self) -> bool:
        return True
