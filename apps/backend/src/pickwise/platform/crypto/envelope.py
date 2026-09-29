# SPDX-License-Identifier: AGPL-3.0-only
"""AES-256-GCM with a versioned header.

Layout: ``0x01 | key_version (4 bytes, big-endian) | nonce (12) | ciphertext+tag``.
The AAD binds a ciphertext to where it lives (table, column, row id), so a value
copied into another row or column fails to decrypt.
"""

import os
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_FORMAT = 1
_HEADER = struct.Struct(">BI")
_NONCE_BYTES = 12


class CiphertextError(ValueError):
    """Malformed ciphertext, unknown key version, or failed authentication."""


@dataclass(frozen=True, slots=True, repr=False)
class DataKey:
    version: int
    material: bytes

    def __repr__(self) -> str:  # never print key material
        return f"DataKey(version={self.version})"


def encrypt(key: DataKey, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return (
        _HEADER.pack(_FORMAT, key.version)
        + nonce
        + AESGCM(key.material).encrypt(nonce, plaintext, aad)
    )


def key_version_of(ciphertext: bytes) -> int:
    if len(ciphertext) < _HEADER.size + _NONCE_BYTES + 16:
        raise CiphertextError("ciphertext too short")
    fmt, version = _HEADER.unpack_from(ciphertext)
    if fmt != _FORMAT:
        raise CiphertextError(f"unknown ciphertext format {fmt}")
    return int(version)


def decrypt(keys: dict[int, DataKey], ciphertext: bytes, aad: bytes) -> bytes:
    version = key_version_of(ciphertext)
    key = keys.get(version)
    if key is None:
        raise CiphertextError(f"no key with version {version}")
    nonce = ciphertext[_HEADER.size : _HEADER.size + _NONCE_BYTES]
    try:
        return AESGCM(key.material).decrypt(nonce, ciphertext[_HEADER.size + _NONCE_BYTES :], aad)
    except InvalidTag as exc:
        raise CiphertextError("authentication failed") from exc
