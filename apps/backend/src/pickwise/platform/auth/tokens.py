# SPDX-License-Identifier: AGPL-3.0-only
"""Opaque secrets: session tokens, single-use auth tokens, recovery codes, API keys.

Only a hash is ever stored. Tokens are 32 random bytes, so an unsalted SHA-256 is
enough to make a leaked hash useless.
"""

import base64
import hashlib
import secrets


def new_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
