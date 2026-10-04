# SPDX-License-Identifier: AGPL-3.0-only
"""Application-level field encryption for SQLAlchemy models (CLAUDE.md rule 13; ADR 0014).

``EncryptedStr`` columns hold AES-256-GCM ciphertext. Each ciphertext is bound to its
table, column, tenant and row id through the AAD, so a value copied into another row,
column or tenant fails to decrypt.

How it works with the ORM:
- Code sets ``employee.pan = "ABCDE1234F"`` and flushes inside ``field_crypto(...)``.
  Before the INSERT/UPDATE the plaintext is swapped for a sealed marker that the column
  type encrypts with the row's id (assigned client-side if it isn't yet); afterwards the
  plaintext is restored on the instance, so nothing about the object changes.
- On load, ciphertext is decrypted when a ``field_crypto`` context is active; otherwise
  the attribute stays an opaque ``Ciphertext``, so listings never need keys.
- A plaintext string can never reach the database: outside the ORM flush the column type
  refuses it.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy import LargeBinary, event, inspect
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Mapper, attributes
from sqlalchemy.types import TypeDecorator

from pickwise.platform.crypto.keys import Keyring
from pickwise.shared.ids import uuid7


class FieldCryptoError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FieldCryptoContext:
    keyring: Keyring
    tenant_id: uuid.UUID


_current: ContextVar[FieldCryptoContext | None] = ContextVar("pickwise_field_crypto", default=None)


@contextmanager
def field_crypto(keyring: Keyring, tenant_id: uuid.UUID) -> Iterator[None]:
    """Make ``keyring`` (a tenant's keys) available to encrypted columns in this block."""
    token = _current.set(FieldCryptoContext(keyring, tenant_id))
    try:
        yield
    finally:
        _current.reset(token)


def current_context() -> FieldCryptoContext:
    context = _current.get()
    if context is None:
        raise FieldCryptoError(
            "no field_crypto(...) context: encrypted columns need the tenant keys"
        )
    return context


def field_aad(table: str, column: str, tenant_id: uuid.UUID, row_id: uuid.UUID) -> bytes:
    return f"{table}.{column}:{tenant_id}:{row_id}".encode()


class Ciphertext:
    """An encrypted value that hasn't been opened (no keys were active when it was loaded)."""

    __slots__ = ("data",)

    def __init__(self, data: bytes) -> None:
        self.data = data

    def __repr__(self) -> str:  # never print the bytes
        return f"Ciphertext({len(self.data)} bytes)"

    def open(self, table: str, column: str, row_id: uuid.UUID) -> str:
        context = current_context()
        aad = field_aad(table, column, context.tenant_id, row_id)
        return context.keyring.decrypt(self.data, aad).decode()


class _Sealed:
    """Marks plaintext for encryption at flush time, with the AAD that binds it to its row."""

    __slots__ = ("column", "plaintext", "row_id", "table")

    def __init__(self, plaintext: str, table: str, column: str, row_id: uuid.UUID) -> None:
        self.plaintext = plaintext
        self.table = table
        self.column = column
        self.row_id = row_id

    def __repr__(self) -> str:
        return "_Sealed(...)"


class EncryptedStr(TypeDecorator[Any]):
    """A text value stored as ciphertext (bytea). Pair it with ``*_last4`` and ``*_bidx``."""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        if isinstance(value, _Sealed):
            context = current_context()
            aad = field_aad(value.table, value.column, context.tenant_id, value.row_id)
            return context.keyring.encrypt(value.plaintext.encode(), aad)
        if isinstance(value, Ciphertext):
            return value.data
        raise FieldCryptoError(
            "plaintext can't be written to an encrypted column outside an ORM flush"
        )

    def process_result_value(self, value: Any, dialect: Dialect) -> Ciphertext | None:
        return None if value is None else Ciphertext(bytes(value))


def _encrypted_keys(mapper: Mapper[Any]) -> list[tuple[str, str]]:
    """(attribute key, column name) for each EncryptedStr column of the mapper."""
    found = []
    for attr in mapper.column_attrs:
        column = attr.columns[0]
        if isinstance(column.type, EncryptedStr):
            found.append((attr.key, column.name))
    return found


def _table_of(mapper: Mapper[Any]) -> str:
    return str(mapper.local_table.fullname)  # type: ignore[attr-defined]


def _seal(mapper: Mapper[Any], _connection: Any, target: Any) -> None:
    keys = _encrypted_keys(mapper)
    if not keys:
        return
    state = inspect(target)
    pending: dict[str, str] = {}
    for key, column in keys:
        value = state.dict.get(key)
        if not isinstance(value, str):
            continue
        if not attributes.get_history(target, key).has_changes():
            continue
        if getattr(target, "id", None) is None:
            target.id = uuid7()
        state.dict[key] = _Sealed(value, _table_of(mapper), column, target.id)
        pending[key] = value
    if pending:
        state.info["pickwise_plaintext"] = pending


def _restore(_mapper: Mapper[Any], _connection: Any, target: Any) -> None:
    state = inspect(target)
    pending: dict[str, str] | None = state.info.pop("pickwise_plaintext", None)
    for key, value in (pending or {}).items():
        attributes.set_committed_value(target, key, value)


def _open(target: Any, *_args: Any) -> None:
    mapper = inspect(target).mapper
    keys = _encrypted_keys(mapper)
    if not keys or _current.get() is None:
        return
    state = inspect(target)
    for key, column in keys:
        value = state.dict.get(key)
        if isinstance(value, Ciphertext):
            attributes.set_committed_value(
                target, key, value.open(_table_of(mapper), column, target.id)
            )


def reveal(target: Any) -> None:
    """Decrypt any still-opaque encrypted attributes of a loaded instance (needs a context)."""
    _open(target)


event.listen(Mapper, "before_insert", _seal)
event.listen(Mapper, "before_update", _seal)
event.listen(Mapper, "after_insert", _restore)
event.listen(Mapper, "after_update", _restore)
event.listen(Mapper, "load", _open)
event.listen(Mapper, "refresh", _open)
