# SPDX-License-Identifier: AGPL-3.0-only
"""Display masks. Restricted values are shown masked unless the caller holds a reveal
permission (and that reveal is audited as ``pii.reveal``)."""


def last4(value: str | None) -> str | None:
    """The last four characters, e.g. for a bank account or PAN stored beside its ciphertext."""
    if not value:
        return None
    compact = "".join(ch for ch in value if not ch.isspace() and ch != "-")
    return compact[-4:] if compact else None


def mask_tail(value: str | None, visible: int = 4, fill: str = "•") -> str:
    """``••••••1234``: everything but the last ``visible`` characters hidden."""
    if not value:
        return ""
    compact = "".join(ch for ch in value if not ch.isspace() and ch != "-")
    if visible <= 0 or len(compact) <= visible:
        return fill * len(compact)
    return fill * (len(compact) - visible) + compact[-visible:]


def mask_email(value: str | None) -> str:
    if not value or "@" not in value:
        return mask_tail(value, 0)
    local, _, domain = value.partition("@")
    return f"{local[:1]}{'•' * max(len(local) - 1, 1)}@{domain}"


def mask_phone(value: str | None) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return mask_tail(digits, 2) if digits else ""
