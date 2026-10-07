# SPDX-License-Identifier: AGPL-3.0-only
"""The tenant keys made available by ``field_crypto(...)`` (platform's reveal endpoint)."""

import uuid

from pickwise.platform.crypto import Keyring
from pickwise.platform.crypto.fields import current_context


def keys_and_tenant() -> tuple[Keyring, uuid.UUID]:
    context = current_context()
    return context.keyring, context.tenant_id
