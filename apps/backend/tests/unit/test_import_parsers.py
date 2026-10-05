# SPDX-License-Identifier: AGPL-3.0-only
"""Reading CSV and XLSX imports."""

import datetime
import io

import openpyxl
import pytest

from pickwise.platform.files.types import XLSX
from pickwise.platform.imports.parsers import ImportFileError, normalise_header, parse
from pickwise.platform.imports.registry import ImportColumn

COLUMNS = (
    ImportColumn("email", "Email", required=True),
    ImportColumn("name", "Name", required=True),
    ImportColumn("start_date", "Start date"),
)


def rows(
    content: bytes, mime: str = "text/csv", max_rows: int = 100
) -> list[tuple[int, dict[str, str]]]:
    parsed = parse(io.BytesIO(content), mime, COLUMNS, max_rows=max_rows)
    return [(r.line, r.values) for r in parsed.rows]


def test_headers_are_normalised() -> None:
    assert normalise_header("  Start Date ") == "start_date"
    assert normalise_header("start-date") == "start_date"
    assert normalise_header(None) == ""


def test_csv_with_bom_blank_rows_and_short_rows() -> None:
    content = "﻿Email, Name ,Start Date\na@x.test,Asha,2026-01-05\n\n,,\nb@x.test,Bala\n".encode()
    assert rows(content) == [
        (2, {"email": "a@x.test", "name": "Asha", "start_date": "2026-01-05"}),
        (5, {"email": "b@x.test", "name": "Bala", "start_date": ""}),
    ]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "empty"),
        (b"email,name,extra\n", "Unknown columns: extra"),
        (b"email\n", "Missing required columns: name"),
        (b"email,name,name\n", "Duplicate columns: name"),
        (b"email,,name\n", "needs a header"),
        (b"email,name\na@x.test,A,surplus\n", "Row 2 has more values"),
        (b"email,name\n\xff\xfe\n", "UTF-8"),
    ],
)
def test_csv_file_problems(content: bytes, message: str) -> None:
    with pytest.raises(ImportFileError, match=message):
        rows(content)


def test_the_row_cap() -> None:
    content = b"email,name\n" + b"".join(b"a%d@x.test,N\n" % i for i in range(5))
    assert len(rows(content, max_rows=5)) == 5
    with pytest.raises(ImportFileError, match="more than 4 rows"):
        rows(content, max_rows=4)


def workbook(*sheet_rows: list[object]) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for r in sheet_rows:
        sheet.append(r)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_xlsx_values_become_plain_strings() -> None:
    content = workbook(
        ["Email", "Name", "Start Date"],
        ["a@x.test", "Asha", datetime.date(2026, 1, 5)],
        [None, None, None],
        ["b@x.test", 42, datetime.datetime(2026, 2, 3, 9, 30)],
    )
    assert rows(content, XLSX) == [
        (2, {"email": "a@x.test", "name": "Asha", "start_date": "2026-01-05"}),
        (4, {"email": "b@x.test", "name": "42", "start_date": "2026-02-03T09:30:00"}),
    ]


def test_xlsx_problems() -> None:
    with pytest.raises(ImportFileError, match="readable Excel"):
        rows(b"not a workbook", XLSX)
    with pytest.raises(ImportFileError, match="Missing required"):
        rows(workbook(["Email"]), XLSX)
    with pytest.raises(ImportFileError, match="Unknown columns"):
        rows(workbook(["Email", "Name", "Salary"]), XLSX)
