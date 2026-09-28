# SPDX-License-Identifier: AGPL-3.0-only
"""Minimal async clamd client (INSTREAM and PING over TCP).

Protocol: https://docs.clamav.net/manual/Usage/Scanning.html#clamd
We use the null-terminated ("z") command form.
"""

import asyncio
import struct
from collections.abc import AsyncIterator

from pickwise.platform.scanning.base import ScannerError, ScanResult, ScanVerdict

# clamd's default StreamMaxLength is 25 MiB; keep chunks well below it.
_CHUNK_LIMIT = 64 * 1024


class ClamAVScanner:
    name = "clamav"

    def __init__(self, host: str, port: int, timeout_seconds: float) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout_seconds

    async def scan(self, chunks: AsyncIterator[bytes]) -> ScanResult:
        try:
            async with asyncio.timeout(self._timeout):
                reader, writer = await asyncio.open_connection(self._host, self._port)
                try:
                    writer.write(b"zINSTREAM\0")
                    async for chunk in chunks:
                        for start in range(0, len(chunk), _CHUNK_LIMIT):
                            part = chunk[start : start + _CHUNK_LIMIT]
                            writer.write(struct.pack("!L", len(part)) + part)
                            await writer.drain()
                    writer.write(struct.pack("!L", 0))
                    await writer.drain()
                    reply = await reader.readuntil(b"\0")
                finally:
                    writer.close()
                    await writer.wait_closed()
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            raise ScannerError(f"clamd unavailable: {type(exc).__name__}") from exc
        return _parse_reply(reply.rstrip(b"\0").decode("utf-8", "replace"))

    async def ping(self) -> bool:
        try:
            async with asyncio.timeout(self._timeout):
                reader, writer = await asyncio.open_connection(self._host, self._port)
                try:
                    writer.write(b"zPING\0")
                    await writer.drain()
                    reply = await reader.readuntil(b"\0")
                finally:
                    writer.close()
                    await writer.wait_closed()
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            return False
        return reply.rstrip(b"\0") == b"PONG"


def _parse_reply(reply: str) -> ScanResult:
    # "stream: OK" | "stream: <signature> FOUND" | "<message> ERROR"
    if reply.endswith("ERROR"):
        raise ScannerError(f"clamd error: {reply}")
    if reply == "stream: OK":
        return ScanResult(ScanVerdict.CLEAN)
    if reply.startswith("stream: ") and reply.endswith(" FOUND"):
        signature = reply.removeprefix("stream: ").removesuffix(" FOUND")
        return ScanResult(ScanVerdict.INFECTED, signature)
    raise ScannerError(f"unexpected clamd reply: {reply!r}")
