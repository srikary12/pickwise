# SPDX-License-Identifier: AGPL-3.0-only
"""Envelope encryption and blind indexes (CLAUDE.md "Crypto", rule 13; ADR 0006).

- The KEK (``PICKWISE_KEK``) is the only global secret. It only wraps data keys.
- Data keys live wrapped in ``platform.platform_keys`` (global secrets such as TOTP
  seeds) and ``platform.tenant_keys`` (per tenant: a data key and a separate
  blind-index key).
- Ciphertexts carry their key version, so old versions stay readable after a rotation.
- Plaintext keys and secrets are never logged.
"""

from pickwise.platform.crypto.envelope import (
    CiphertextError,
    DataKey,
    decrypt,
    encrypt,
    key_version_of,
)
from pickwise.platform.crypto.kek import EnvKek, KeyEncryptionKey, kek_from_settings
from pickwise.platform.crypto.keys import (
    BLIND_INDEX,
    DATA,
    Keyring,
    blind_index,
    load_platform_keyring,
    load_tenant_keyring,
    new_data_key,
)

__all__ = [
    "BLIND_INDEX",
    "DATA",
    "CiphertextError",
    "DataKey",
    "EnvKek",
    "KeyEncryptionKey",
    "Keyring",
    "blind_index",
    "decrypt",
    "encrypt",
    "kek_from_settings",
    "key_version_of",
    "load_platform_keyring",
    "load_tenant_keyring",
    "new_data_key",
]
