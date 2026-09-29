# SPDX-License-Identifier: AGPL-3.0-only
"""PII classification loaded from db/pii_classification.yaml (ADR 0005).

The YAML is the source of truth. Migrations turn it into '@pii …' column comments,
audit redaction reads those comments, and ``render_markdown`` produces the
generated section of docs/DATA_MODEL.md.
"""

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class Level(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def is_personal(self) -> bool:
        """Levels that get a '@pii' comment and are redacted in audit diffs."""
        return self in (Level.CONFIDENTIAL, Level.RESTRICTED)


@dataclass(frozen=True, slots=True, order=True)
class Column:
    schema: str
    table: str
    column: str
    level: Level

    @property
    def qualified_table(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def comment(self) -> str:
        return f"@pii {self.level.value}"


class ClassificationError(ValueError):
    pass


def default_path() -> Path:
    """The YAML lives in db/; containers mount or copy it to /app/db."""
    if override := os.environ.get("PICKWISE_PII_CLASSIFICATION"):
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "db" / "pii_classification.yaml"
        if candidate.exists():
            return candidate
    return Path("/app/db/pii_classification.yaml")


def load(path: Path | None = None) -> list[Column]:
    raw: Any = yaml.safe_load((path or default_path()).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ClassificationError("expected a mapping with version: 1")
    tables = raw.get("tables")
    if not isinstance(tables, dict):
        raise ClassificationError("expected a 'tables' mapping")

    columns: list[Column] = []
    for qualified, cols in tables.items():
        schema, dot, table = str(qualified).partition(".")
        if not dot or not schema or not table or "." in table:
            raise ClassificationError(f"table key must be schema.table, got {qualified!r}")
        if not isinstance(cols, dict) or not cols:
            raise ClassificationError(f"{qualified}: expected a non-empty column mapping")
        for column, level in cols.items():
            try:
                parsed = Level(level)
            except ValueError as exc:
                raise ClassificationError(f"{qualified}.{column}: unknown level {level!r}") from exc
            if parsed is Level.INTERNAL:
                raise ClassificationError(
                    f"{qualified}.{column}: internal is the default; don't list it"
                )
            columns.append(Column(schema, table, str(column), parsed))
    return sorted(columns)


def personal_columns(columns: list[Column]) -> list[Column]:
    return [c for c in columns if c.level.is_personal]


BEGIN_MARKER = "<!-- BEGIN GENERATED: make docs-pii"
END_MARKER = "<!-- END GENERATED -->"

_BADGES = {
    Level.RESTRICTED: "🔒 restricted",
    Level.CONFIDENTIAL: "🟠 confidential",
    Level.PUBLIC: "⚪ public",
}


def render_markdown(columns: list[Column]) -> str:
    lines = ["Columns not listed are 🟢 internal. Source: `db/pii_classification.yaml`."]
    current_schema = None
    for col in columns:
        if col.schema != current_schema:
            current_schema = col.schema
            lines += ["", f"### `{col.schema}`", "", "| Table | Column | Level |", "|---|---|---|"]
        lines.append(f"| {col.table} | {col.column} | {_BADGES[col.level]} |")
    return "\n".join(lines).strip() + "\n"


def replace_generated_section(document: str, body: str) -> str:
    start = document.find(BEGIN_MARKER)
    end = document.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise ClassificationError("generated-section markers not found in the document")
    head_end = document.index("\n", start) + 1
    return document[:head_end] + body + document[end:]
