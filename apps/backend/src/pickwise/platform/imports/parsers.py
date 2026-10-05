# SPDX-License-Identifier: AGPL-3.0-only
"""Reading CSV and XLSX imports into rows of strings.

Everything is bounded: file size is capped by the files pipeline, rows by
``IMPORTS_MAX_ROWS``, cells at ``MAX_CELL``. XLSX is read with openpyxl in read-only,
data-only mode: formulas are not evaluated (their cached values are read) and external
links are never followed. Dates become ISO strings, numbers plain text.
"""

import csv
import datetime
import io
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import IO

import openpyxl

from pickwise.platform.files.types import XLSX
from pickwise.platform.imports.registry import ImportColumn, ImportRow

MAX_CELL = 2000
MAX_COLUMNS = 100


class ImportFileError(ValueError):
    """The file as a whole can't be imported. The message is safe to show."""


@dataclass(frozen=True, slots=True)
class Parsed:
    header: list[str]  # normalised
    rows: Iterator[ImportRow]


def normalise_header(value: object) -> str:
    text = "" if value is None else str(value)
    return "_".join(text.strip().lower().replace("-", " ").split())


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat() if value.time() == datetime.time() else value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, Decimal | float | int):
        return str(value)
    return str(value).strip()


def _check_header(header: list[str], columns: tuple[ImportColumn, ...]) -> None:
    if len(header) > MAX_COLUMNS:
        raise ImportFileError("The file has too many columns.")
    if any(not h for h in header):
        raise ImportFileError("Every column needs a header in the first row.")
    duplicates = sorted({h for h in header if header.count(h) > 1})
    if duplicates:
        raise ImportFileError(f"Duplicate columns: {', '.join(duplicates)}.")
    known = {c.name for c in columns}
    unknown = [h for h in header if h not in known]
    if unknown:
        raise ImportFileError(f"Unknown columns: {', '.join(unknown)}.")
    missing = [c.name for c in columns if c.required and c.name not in header]
    if missing:
        raise ImportFileError(f"Missing required columns: {', '.join(missing)}.")


def _rows(
    header: list[str], raw_rows: Iterator[tuple[int, list[str]]], max_rows: int
) -> Iterator[ImportRow]:
    count = 0
    for line, cells in raw_rows:
        if not any(cells):
            continue
        count += 1
        if count > max_rows:
            raise ImportFileError(f"The file has more than {max_rows} rows; split it.")
        if len(cells) > len(header) and any(cells[len(header) :]):
            raise ImportFileError(f"Row {line} has more values than columns.")
        padded = (cells + [""] * len(header))[: len(header)]
        values = {name: value[:MAX_CELL] for name, value in zip(header, padded, strict=True)}
        yield ImportRow(line, values)


def parse(
    stream: IO[bytes],
    mime_type: str,
    columns: tuple[ImportColumn, ...],
    *,
    max_rows: int,
) -> Parsed:
    """Header-checked rows. File-level problems raise ``ImportFileError`` (for XLSX and the
    header of a CSV straight away, for row-count and ragged-row problems while iterating)."""
    stream.seek(0)
    if mime_type == XLSX:
        return _parse_xlsx(stream, columns, max_rows)
    return _parse_csv(stream, columns, max_rows)


def _parse_csv(stream: IO[bytes], columns: tuple[ImportColumn, ...], max_rows: int) -> Parsed:
    try:
        text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
        reader = csv.reader(text)
        first = next(reader, None)
        if first is None:
            raise ImportFileError("The file is empty.")
        header = [normalise_header(h) for h in first]
        _check_header(header, columns)

        def raw() -> Iterator[tuple[int, list[str]]]:
            try:
                for cells in reader:
                    yield reader.line_num, [c.strip() for c in cells]
            except (csv.Error, UnicodeDecodeError) as exc:
                raise ImportFileError("The file isn't valid UTF-8 CSV.") from exc

        return Parsed(header, _rows(header, raw(), max_rows))
    except UnicodeDecodeError as exc:
        raise ImportFileError("The file isn't valid UTF-8 CSV.") from exc


def _parse_xlsx(stream: IO[bytes], columns: tuple[ImportColumn, ...], max_rows: int) -> Parsed:
    try:
        workbook = openpyxl.load_workbook(stream, read_only=True, data_only=True)
    except Exception as exc:
        raise ImportFileError("The file isn't a readable Excel workbook.") from exc
    sheet = workbook.worksheets[0] if workbook.worksheets else None
    if sheet is None:
        raise ImportFileError("The workbook has no sheets.")
    iterator = sheet.iter_rows(values_only=True)
    first = next(iterator, None)
    if first is None:
        raise ImportFileError("The first sheet is empty.")
    trimmed = list(first)
    while trimmed and trimmed[-1] is None:
        trimmed.pop()
    header = [normalise_header(h) for h in trimmed]
    _check_header(header, columns)

    def raw() -> Iterator[tuple[int, list[str]]]:
        for number, cells in enumerate(iterator, start=2):
            yield number, [_cell(c) for c in cells]

    return Parsed(header, _rows(header, raw(), max_rows))
