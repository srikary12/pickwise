# SPDX-License-Identifier: AGPL-3.0-only
"""Server-side type checks: what a file really is, not what the client says it is."""

import zipfile
from pathlib import PurePosixPath
from typing import IO

import puremagic

SNIFF_BYTES = 64 * 1024

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Extensions acceptable for each mime type (lower case, with the dot).
EXTENSIONS: dict[str, frozenset[str]] = {
    "application/pdf": frozenset({".pdf"}),
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "text/plain": frozenset({".txt", ".text", ".log", ".md"}),
    "text/csv": frozenset({".csv"}),
    DOCX: frozenset({".docx"}),
    XLSX: frozenset({".xlsx"}),
}


def extension_of(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower()


def safe_name(filename: str) -> str:
    """The display name: no path, no control characters, bounded length."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '"<>|:*?')
    return (name.strip(". ") or "file")[:200]


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut by the window is fine; anything else is binary.
        if exc.start < len(head) - 4:
            return False
    return True


def sniff_mime(stream: IO[bytes]) -> str | None:
    """The detected mime type of ``stream`` (read from the start), or None if unknown.

    ZIP-based Office files are told apart by their entries, so a renamed .zip can't pass
    as .docx. Plain text has no magic bytes: it counts as text/plain when it is clean UTF-8.
    """
    stream.seek(0)
    head = stream.read(SNIFF_BYTES)
    if not head:
        return None
    if head.startswith(b"PK\x03\x04"):
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        if "[Content_Types].xml" in names and any(n.startswith("word/") for n in names):
            return DOCX
        if "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names):
            return XLSX
        return "application/zip"
    matches = puremagic.magic_string(head)
    if matches:
        return str(matches[0].mime_type)
    return "text/plain" if _looks_like_text(head) else None


def compatible(declared: str | None, sniffed: str | None) -> bool:
    """Does the sniffed type satisfy what the client declared?"""
    if not declared or not sniffed:
        return False
    if declared == sniffed:
        return True
    # CSV is plain text with commas: it can't be told apart by content alone.
    return declared == "text/csv" and sniffed == "text/plain"
