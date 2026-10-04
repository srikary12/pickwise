# SPDX-License-Identifier: AGPL-3.0-only
"""Key rotation (CLAUDE.md rule 13; ADR 0014). Everything here runs in an ops session.

- **KEK rotation** re-wraps every wrapped key under a new KEK. No data is touched.
- **Data keys** rotate by adding a new version and keeping the old one readable
  (status ``rotating``); new writes use the new key, and a batched re-encryption moves
  existing ciphertexts over. The old key is retired only once no ciphertext uses it.
- **Blind-index keys**: during a rotation lookups hash under every non-retired key; a
  batched job recomputes each ``*_bidx`` under the new key, then the old key is retired.

Modules register their encrypted columns in ``ENCRYPTED_FIELDS`` so these jobs can find them.
"""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.crypto.fields import field_aad
from pickwise.platform.crypto.kek import KeyEncryptionKey
from pickwise.platform.crypto.keys import (
    BLIND_INDEX,
    DATA,
    Keyring,
    blind_index,
    load_tenant_keyring,
    platform_key_aad,
    tenant_key_aad,
    wrap_new_key,
)

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
BATCH = 500


def normalize_identifier(value: str) -> str:
    """The canonical form blind indexes are computed over: no spaces or dashes, upper case."""
    return "".join(ch for ch in value if not ch.isspace() and ch != "-").upper()


@dataclass(frozen=True, slots=True)
class EncryptedField:
    """One encrypted column (and its blind index, if any), registered by the owning module."""

    schema: str
    table: str
    enc_column: str
    bidx_column: str | None = None
    normalize: Callable[[str], str] = normalize_identifier

    def __post_init__(self) -> None:
        for name in (self.schema, self.table, self.enc_column, self.bidx_column or "x"):
            if not _IDENT.match(name):
                raise ValueError(f"unsafe identifier {name!r}")

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


class EncryptedFieldRegistry:
    def __init__(self) -> None:
        self._fields: dict[tuple[str, str, str], EncryptedField] = {}

    def register(self, field: EncryptedField) -> None:
        self._fields[(field.schema, field.table, field.enc_column)] = field

    def unregister(self, field: EncryptedField) -> None:
        self._fields.pop((field.schema, field.table, field.enc_column), None)

    def all(self) -> list[EncryptedField]:
        return list(self._fields.values())


ENCRYPTED_FIELDS = EncryptedFieldRegistry()


def blind_lookup_values(keyring: Keyring, value: str) -> list[bytes]:
    """Blind indexes of ``value`` under every non-retired key: query ``bidx IN (…)``."""
    return keyring.blind_indexes(normalize_identifier(value))


# --- KEK rotation ------------------------------------------------------------------------


async def rewrap_all(
    session: AsyncSession, old_kek: KeyEncryptionKey, new_kek: KeyEncryptionKey
) -> dict[str, int]:
    """Re-wrap every platform and tenant key under ``new_kek``. Idempotent per row."""
    counts = {"platform_keys": 0, "tenant_keys": 0}
    rows = (
        await session.execute(
            text("SELECT id, purpose, version, wrapped_key, kek_id FROM platform.platform_keys")
        )
    ).all()
    for key_id, purpose, version, wrapped, kek_id in rows:
        if kek_id == new_kek.kek_id:
            continue
        aad = platform_key_aad(str(purpose), int(version))
        material = old_kek.unwrap(bytes(wrapped), aad)
        await session.execute(
            text("UPDATE platform.platform_keys SET wrapped_key = :w, kek_id = :k WHERE id = :id"),
            {"w": new_kek.wrap(material, aad), "k": new_kek.kek_id, "id": key_id},
        )
        counts["platform_keys"] += 1
    rows = (
        await session.execute(
            text(
                "SELECT tenant_id, id, purpose, version, wrapped_key, kek_id "
                "FROM platform.tenant_keys"
            )
        )
    ).all()
    for tenant_id, key_id, purpose, version, wrapped, kek_id in rows:
        if kek_id == new_kek.kek_id:
            continue
        aad = tenant_key_aad(tenant_id, str(purpose), int(version))
        material = old_kek.unwrap(bytes(wrapped), aad)
        await session.execute(
            text(
                "UPDATE platform.tenant_keys SET wrapped_key = :w, kek_id = :k "
                "WHERE tenant_id = :t AND id = :id"
            ),
            {"w": new_kek.wrap(material, aad), "k": new_kek.kek_id, "t": tenant_id, "id": key_id},
        )
        counts["tenant_keys"] += 1
    return counts


# --- adding a key version --------------------------------------------------------------------


async def _set_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
    )


async def add_tenant_key_version(
    session: AsyncSession, kek: KeyEncryptionKey, tenant_id: uuid.UUID, purpose: str
) -> int:
    """Add a new active version of a tenant's ``data`` or ``blind_index`` key.

    The previous active version becomes ``rotating``: still loaded (decryption and
    blind-index lookups keep working) until ``retire_rotating`` runs.
    """
    if purpose not in (DATA, BLIND_INDEX):
        raise ValueError(f"unknown key purpose {purpose!r}")
    await _set_tenant(session, tenant_id)
    row: int = (
        await session.execute(
            text(
                "SELECT coalesce(max(version), 0) FROM platform.tenant_keys "
                "WHERE tenant_id = :t AND purpose = :p"
            ),
            {"t": tenant_id, "p": purpose},
        )
    ).scalar_one()
    version = int(row) + 1
    already = (
        await session.execute(
            text(
                "SELECT 1 FROM platform.tenant_keys "
                "WHERE tenant_id = :t AND purpose = :p AND status = 'rotating'"
            ),
            {"t": tenant_id, "p": purpose},
        )
    ).first()
    if already:
        raise RuntimeError(
            f"a {purpose} key rotation is already in progress for this tenant; finish it first"
        )
    await session.execute(
        text(
            "UPDATE platform.tenant_keys SET status = 'rotating' "
            "WHERE tenant_id = :t AND purpose = :p AND status = 'active'"
        ),
        {"t": tenant_id, "p": purpose},
    )
    _material, wrapped = wrap_new_key(kek, tenant_key_aad(tenant_id, purpose, version))
    await session.execute(
        text(
            "INSERT INTO platform.tenant_keys "
            "(tenant_id, purpose, version, wrapped_key, kek_id, status) "
            "VALUES (:t, :p, :v, :w, :k, 'active')"
        ),
        {"t": tenant_id, "p": purpose, "v": version, "w": wrapped, "k": kek.kek_id},
    )
    return version


async def retire_rotating(session: AsyncSession, tenant_id: uuid.UUID, purpose: str) -> int:
    await _set_tenant(session, tenant_id)
    result = await session.execute(
        text(
            "UPDATE platform.tenant_keys SET status = 'retired', rotated_at = now() "
            "WHERE tenant_id = :t AND purpose = :p AND status = 'rotating'"
        ),
        {"t": tenant_id, "p": purpose},
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- batched re-encryption and blind-index rebuild --------------------------------------------

_VERSION_SQL = (
    "(get_byte({c}, 1)::bigint * 16777216 + get_byte({c}, 2) * 65536 "
    "+ get_byte({c}, 3) * 256 + get_byte({c}, 4))"
)


async def count_on_old_data_keys(
    session: AsyncSession, tenant_id: uuid.UUID, field: EncryptedField, active_version: int
) -> int:
    await _set_tenant(session, tenant_id)
    version = _VERSION_SQL.format(c=field.enc_column)
    count: int = (
        await session.execute(
            text(
                f"SELECT count(*) FROM {field.qualified} "  # noqa: S608 - identifiers validated
                f"WHERE tenant_id = :t AND {field.enc_column} IS NOT NULL AND {version} <> :v"
            ),
            {"t": tenant_id, "v": active_version},
        )
    ).scalar_one()
    return int(count)


async def reencrypt_field(
    session: AsyncSession, keyring: Keyring, tenant_id: uuid.UUID, field: EncryptedField
) -> int:
    """Re-encrypt rows still on an old data key, one batch. Returns rows changed."""
    await _set_tenant(session, tenant_id)
    active = keyring.active_key(DATA).version
    version = _VERSION_SQL.format(c=field.enc_column)
    rows = (
        await session.execute(
            text(
                f"SELECT id, {field.enc_column} FROM {field.qualified} "  # noqa: S608
                f"WHERE tenant_id = :t AND {field.enc_column} IS NOT NULL AND {version} <> :v "
                "ORDER BY id LIMIT :n FOR UPDATE"
            ),
            {"t": tenant_id, "v": active, "n": BATCH},
        )
    ).all()
    for row_id, ciphertext in rows:
        aad = field_aad(field.qualified, field.enc_column, tenant_id, row_id)
        plaintext = keyring.decrypt(bytes(ciphertext), aad)
        await session.execute(
            text(
                f"UPDATE {field.qualified} SET {field.enc_column} = :c "  # noqa: S608
                "WHERE tenant_id = :t AND id = :id"
            ),
            {"c": keyring.encrypt(plaintext, aad), "t": tenant_id, "id": row_id},
        )
    return len(rows)


async def rebuild_blind_index(
    session: AsyncSession, keyring: Keyring, tenant_id: uuid.UUID, field: EncryptedField
) -> int:
    """Recompute ``*_bidx`` under the active blind-index key for every row (keyset batches).

    Safe to repeat: the result is deterministic. Returns rows processed.
    """
    if field.bidx_column is None:
        return 0
    await _set_tenant(session, tenant_id)
    key = keyring.active_key(BLIND_INDEX)
    done = 0
    last: uuid.UUID | None = None
    while True:
        rows = (
            await session.execute(
                text(
                    f"SELECT id, {field.enc_column} FROM {field.qualified} "  # noqa: S608
                    f"WHERE tenant_id = :t AND {field.enc_column} IS NOT NULL "
                    "AND (CAST(:last AS uuid) IS NULL OR id > :last) ORDER BY id LIMIT :n"
                ),
                {"t": tenant_id, "last": last, "n": BATCH},
            )
        ).all()
        if not rows:
            return done
        for row_id, ciphertext in rows:
            aad = field_aad(field.qualified, field.enc_column, tenant_id, row_id)
            plaintext = keyring.decrypt(bytes(ciphertext), aad).decode()
            await session.execute(
                text(
                    f"UPDATE {field.qualified} SET {field.bidx_column} = :b "  # noqa: S608
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {
                    "b": blind_index(key.material, field.normalize(plaintext)),
                    "t": tenant_id,
                    "id": row_id,
                },
            )
        last = rows[-1][0]
        done += len(rows)


async def reload_keyring(
    session: AsyncSession, kek: KeyEncryptionKey, tenant_id: uuid.UUID
) -> Keyring:
    await _set_tenant(session, tenant_id)
    return await load_tenant_keyring(session, kek, tenant_id)
