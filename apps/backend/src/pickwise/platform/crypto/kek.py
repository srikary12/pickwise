# SPDX-License-Identifier: AGPL-3.0-only
"""The key-encryption key. Only wraps and unwraps 32-byte data keys.

``EnvKek`` reads ``PICKWISE_KEK`` (dev, small self-hosted installs). A cloud-KMS
implementation of the same protocol arrives in the hardening phase.
"""

import base64
import hashlib
import os
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pickwise.shared.settings import Settings

_NONCE_BYTES = 12


class KekUnavailableError(RuntimeError):
    pass


class KeyEncryptionKey(Protocol):
    kek_id: str

    def wrap(self, data_key: bytes, aad: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes, aad: bytes) -> bytes: ...


class EnvKek:
    def __init__(self, material: bytes) -> None:
        if len(material) != 32:
            raise KekUnavailableError("PICKWISE_KEK must decode to exactly 32 bytes")
        self._aead = AESGCM(material)
        # Identifies which KEK wrapped a key without revealing anything about it.
        self.kek_id = "env:" + hashlib.sha256(b"pickwise-kek-id" + material).hexdigest()[:16]

    @classmethod
    def from_base64(cls, value: str) -> "EnvKek":
        try:
            return cls(base64.b64decode(value, validate=True))
        except (ValueError, TypeError) as exc:
            raise KekUnavailableError("PICKWISE_KEK is not valid base64") from exc

    def wrap(self, data_key: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, data_key, aad)

    def unwrap(self, wrapped: bytes, aad: bytes) -> bytes:
        return self._aead.decrypt(wrapped[:_NONCE_BYTES], wrapped[_NONCE_BYTES:], aad)


def kek_from_settings(settings: Settings) -> KeyEncryptionKey:
    value = settings.pickwise_kek.get_secret_value()
    if not value:
        raise KekUnavailableError("PICKWISE_KEK is not set (run `make dev` to generate .env)")
    return EnvKek.from_base64(value)
