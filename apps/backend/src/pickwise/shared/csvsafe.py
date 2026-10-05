# SPDX-License-Identifier: AGPL-3.0-only
"""Writing untrusted text into CSV that people open in spreadsheets (formula injection)."""


def safe_cell(value: str) -> str:
    """Prefix values a spreadsheet would run as a formula (=, +, -, @, tab, CR) with a quote."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
