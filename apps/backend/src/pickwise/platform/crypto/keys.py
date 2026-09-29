# SPDX-License-Identifier: AGPL-3.0-only
"""Keyrings: the unwrapped data keys of the platform or of one tenant.

Rows are read under the caller's own session (tenant keys are RLS-protected), so a
tenant's keys can only be loaded inside that tenant's context or an ops session.
"""

import hashlib
import hmac
import os
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.crypto.envelope import DataKey, decrypt, encrypt
from pickwise.platform.crypto.kek import KeyEncryptionKey

DATA = "data"
BLIND_INDEX = "blind_index"


class KeyMissingError(RuntimeError):
    pass


def new_data_key() -> bytes:
    return os.urandom(32)


def platform_key_aad(purpose: str, version: int) -> bytes:
    return f"platform_keys:{purpose}:{version}".encode()


def tenant_key_aad(tenant_id: uuid.UUID, purpose: str, version: int) -> bytes:
    return f"tenant_keys:{tenant_id}:{purpose}:{version}".encode()


@dataclass(frozen=True, slots=True)
class Keyring:
    """Every non-retired key per purpose; ``active`` is the one new data uses."""

    keys: dict[str, dict[int, DataKey]] = field(default_factory=dict)
    active: dict[str, int] = field(default_factory=dict)

    def active_key(self, purpose: str) -> DataKey:
        version = self.active.get(purpose)
        if version is None:
            raise KeyMissingError(f"no active {purpose} key")
        return self.keys[purpose][version]

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        return encrypt(self.active_key(DATA), plaintext, aad)

    def decrypt(self, ciphertext: bytes, aad: bytes) -> bytes:
        return decrypt(self.keys.get(DATA, {}), ciphertext, aad)

    def blind_indexes(self, value: str) -> list[bytes]:
        """The value's blind index under every non-retired key (for lookups during rotation)."""
        return [blind_index(k.material, value) for k in self.keys.get(BLIND_INDEX, {}).values()]


def blind_index(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode(), hashlib.sha256).digest()


def _build(
    rows: Sequence[Sequence[Any]],
    kek: KeyEncryptionKey,
    aad_for: Callable[[str, int], bytes],
) -> Keyring:
    keys: dict[str, dict[int, DataKey]] = {}
    active: dict[str, int] = {}
    for purpose, version, wrapped, status in rows:
        material = kek.unwrap(bytes(wrapped), aad_for(str(purpose), int(version)))
        keys.setdefault(str(purpose), {})[int(version)] = DataKey(int(version), material)
        if status == "active":
            active[str(purpose)] = int(version)
    return Keyring(keys, active)


def wrap_new_key(kek: KeyEncryptionKey, aad: bytes) -> tuple[bytes, bytes]:
    """A fresh data key and its wrapped form (store only the wrapped one)."""
    material = new_data_key()
    return material, kek.wrap(material, aad)


async def load_platform_keyring(session: AsyncSession, kek: KeyEncryptionKey) -> Keyring:
    rows = (
        await session.execute(
            text(
                "SELECT purpose, version, wrapped_key, status FROM platform.platform_keys "
                "WHERE status <> 'retired'"
            )
        )
    ).all()
    return _build(rows, kek, platform_key_aad)


async def load_tenant_keyring(
    session: AsyncSession, kek: KeyEncryptionKey, tenant_id: uuid.UUID
) -> Keyring:
    rows = (
        await session.execute(
            text(
                "SELECT purpose, version, wrapped_key, status FROM platform.tenant_keys "
                "WHERE tenant_id = :t AND status <> 'retired'"
            ),
            {"t": tenant_id},
        )
    ).all()

    def aad(purpose: str, version: int) -> bytes:
        return tenant_key_aad(tenant_id, purpose, version)

    return _build(rows, kek, aad)
