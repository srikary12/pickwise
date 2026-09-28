# SPDX-License-Identifier: AGPL-3.0-only
"""Virus-scanner interface shared by the stub and the ClamAV implementation."""

import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ScanVerdict(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"


@dataclass(frozen=True, slots=True)
class ScanResult:
    verdict: ScanVerdict
    signature: str | None = None

    @property
    def infected(self) -> bool:
        return self.verdict is ScanVerdict.INFECTED


class ScannerError(Exception):
    """The scanner could not produce a verdict (unreachable, timeout, protocol error)."""


class Scanner(Protocol):
    name: str

    async def scan(self, chunks: AsyncIterator[bytes]) -> ScanResult: ...

    async def ping(self) -> bool: ...


# The EICAR anti-malware test string, stored encoded so the source file itself
# doesn't trip antivirus software on contributors' machines or in CI.
_EICAR_B64 = (
    "WDVPIVAlQEFQWzRcUFpYNTQoUF4pN0NDKTd9JEVJQ0FSLVNUQU5EQVJELUFOVElWSVJVUy1URVNULUZJTEUhJEgrSCo="
)


def eicar_bytes() -> bytes:
    return base64.b64decode(_EICAR_B64)
