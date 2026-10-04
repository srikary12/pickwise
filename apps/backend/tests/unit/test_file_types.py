# SPDX-License-Identifier: AGPL-3.0-only
import io
import zipfile

import pytest

from pickwise.platform.files import types


def sniff(data: bytes) -> str | None:
    return types.sniff_mime(io.BytesIO(data))


def zipped(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "<x/>")
    return buffer.getvalue()


def test_known_formats() -> None:
    assert sniff(b"%PDF-1.7\n%%EOF") == "application/pdf"
    assert sniff(b"\x89PNG\r\n\x1a\n" + b"\0" * 16) == "image/png"
    assert sniff(b"\xff\xd8\xff\xe0" + b"\0" * 16) == "image/jpeg"


def test_office_files_are_told_apart_by_their_entries() -> None:
    assert sniff(zipped("[Content_Types].xml", "word/document.xml")) == types.DOCX
    assert sniff(zipped("[Content_Types].xml", "xl/workbook.xml")) == types.XLSX
    assert sniff(zipped("a.txt")) == "application/zip"


def test_plain_text_and_binary() -> None:
    assert sniff(b"name,email\nAsha,a@example.test\n") == "text/plain"
    assert sniff("नमस्ते\n".encode()) == "text/plain"
    assert sniff(b"\x00\x01\x02\x03 binary") is None
    assert sniff(b"") is None


def test_compatibility() -> None:
    assert types.compatible("application/pdf", "application/pdf")
    assert types.compatible("text/csv", "text/plain")
    assert not types.compatible("application/pdf", "image/png")
    assert not types.compatible("text/plain", "text/csv")
    assert not types.compatible(None, "text/plain")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\a\\cv.pdf", "cv.pdf"),
        ('we"ird<na>me.pdf', "weirdname.pdf"),
        ("   ", "file"),
        ("a" * 400 + ".pdf", ("a" * 400 + ".pdf")[:200]),
    ],
)
def test_safe_name(raw: str, expected: str) -> None:
    assert types.safe_name(raw) == expected
