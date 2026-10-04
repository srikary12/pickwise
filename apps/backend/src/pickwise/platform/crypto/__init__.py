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
from pickwise.platform.crypto.fields import (
    Ciphertext,
    EncryptedStr,
    FieldCryptoError,
    field_aad,
    field_crypto,
    reveal,
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
from pickwise.platform.crypto.masking import last4, mask_email, mask_phone, mask_tail
from pickwise.platform.crypto.rotation import (
    ENCRYPTED_FIELDS,
    EncryptedField,
    blind_lookup_values,
    normalize_identifier,
)

__all__ = [
    "BLIND_INDEX",
    "DATA",
    "ENCRYPTED_FIELDS",
    "Ciphertext",
    "CiphertextError",
    "DataKey",
    "EncryptedField",
    "EncryptedStr",
    "EnvKek",
    "FieldCryptoError",
    "KeyEncryptionKey",
    "Keyring",
    "blind_index",
    "blind_lookup_values",
    "decrypt",
    "encrypt",
    "field_aad",
    "field_crypto",
    "kek_from_settings",
    "key_version_of",
    "last4",
    "load_platform_keyring",
    "load_tenant_keyring",
    "mask_email",
    "mask_phone",
    "mask_tail",
    "new_data_key",
    "normalize_identifier",
    "reveal",
]
