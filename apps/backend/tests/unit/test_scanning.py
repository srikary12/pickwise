# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import struct
from collections.abc import AsyncIterator

import pytest

from pickwise.platform.scanning import (
    ClamAVScanner,
    ScannerError,
    ScanVerdict,
    StubScanner,
    build_scanner,
    eicar_bytes,
    iter_bytes,
)
from pickwise.platform.scanning.clamav import _parse_reply
from pickwise.platform.scanning.stub import STUB_WARNING
from pickwise.shared.settings import ScannerKind, Settings


def test_eicar_is_the_standard_68_byte_test_string() -> None:
    data = eicar_bytes()
    assert len(data) == 68
    assert data.startswith(b"X5O!P%@AP")
    assert data.endswith(b"$H+H*")


async def test_stub_flags_eicar() -> None:
    result = await StubScanner().scan(iter_bytes(eicar_bytes()))
    assert result.verdict is ScanVerdict.INFECTED
    assert result.infected


async def test_stub_flags_eicar_split_across_chunks() -> None:
    payload = b"prefix " + eicar_bytes() + b" suffix"
    result = await StubScanner().scan(iter_bytes(payload, chunk_size=5))
    assert result.infected


async def test_stub_passes_other_content() -> None:
    result = await StubScanner().scan(iter_bytes(b"just a resume" * 1000))
    assert result.verdict is ScanVerdict.CLEAN


async def test_stub_warns_loudly_on_every_scan(caplog: pytest.LogCaptureFixture) -> None:
    scanner = StubScanner()
    await scanner.scan(iter_bytes(b"a"))
    await scanner.scan(iter_bytes(b"b"))
    assert sum(STUB_WARNING in r.getMessage() for r in caplog.records) == 2


@pytest.mark.parametrize(
    ("reply", "verdict", "signature"),
    [
        ("stream: OK", ScanVerdict.CLEAN, None),
        ("stream: Eicar-Test-Signature FOUND", ScanVerdict.INFECTED, "Eicar-Test-Signature"),
        ("stream: Win.Test.EICAR_HDB-1 FOUND", ScanVerdict.INFECTED, "Win.Test.EICAR_HDB-1"),
    ],
)
def test_parse_clamd_reply(reply: str, verdict: ScanVerdict, signature: str | None) -> None:
    result = _parse_reply(reply)
    assert result.verdict is verdict
    assert result.signature == signature


@pytest.mark.parametrize("reply", ["INSTREAM size limit exceeded. ERROR", "garbage"])
def test_parse_clamd_reply_errors(reply: str) -> None:
    with pytest.raises(ScannerError):
        _parse_reply(reply)


async def _fake_clamd(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Speaks just enough of the clamd protocol: zPING and zINSTREAM."""
    command = await reader.readuntil(b"\0")
    if command == b"zPING\0":
        writer.write(b"PONG\0")
    elif command == b"zINSTREAM\0":
        body = b""
        while True:
            (size,) = struct.unpack("!L", await reader.readexactly(4))
            if size == 0:
                break
            body += await reader.readexactly(size)
        found = eicar_bytes() in body
        writer.write(b"stream: Eicar-Test-Signature FOUND\0" if found else b"stream: OK\0")
    await writer.drain()
    writer.close()


@pytest.fixture
async def fake_clamd() -> AsyncIterator[tuple[str, int]]:
    server = await asyncio.start_server(_fake_clamd, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield "127.0.0.1", port


async def test_clamav_adapter_speaks_instream(fake_clamd: tuple[str, int]) -> None:
    host, port = fake_clamd
    scanner = ClamAVScanner(host, port, timeout_seconds=5)
    assert await scanner.ping()
    infected = await scanner.scan(iter_bytes(eicar_bytes(), chunk_size=7))
    assert infected.infected
    assert infected.signature == "Eicar-Test-Signature"
    clean = await scanner.scan(iter_bytes(b"harmless"))
    assert not clean.infected


async def test_clamav_adapter_unreachable_raises() -> None:
    scanner = ClamAVScanner("127.0.0.1", 1, timeout_seconds=1)
    assert not await scanner.ping()
    with pytest.raises(ScannerError):
        await scanner.scan(iter_bytes(b"x"))


def test_build_scanner_follows_settings() -> None:
    assert isinstance(build_scanner(Settings(scanner=ScannerKind.STUB)), StubScanner)
    assert isinstance(build_scanner(Settings(scanner=ScannerKind.CLAMAV)), ClamAVScanner)
    assert isinstance(build_scanner(Settings(), ScannerKind.CLAMAV), ClamAVScanner)
