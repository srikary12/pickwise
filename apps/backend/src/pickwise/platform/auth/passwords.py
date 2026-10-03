# SPDX-License-Identifier: AGPL-3.0-only
"""argon2id password hashing and the password policy."""

import hashlib

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from pickwise.shared.logging import get_logger

MIN_LENGTH = 12
MAX_LENGTH = 256

# OWASP-recommended argon2id parameters (64 MiB, 3 iterations, 1 lane).
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=1)
# Spent on unknown emails so a login takes the same time either way.
_DUMMY_HASH = _hasher.hash("pickwise-timing-equaliser-not-a-real-password")

log = get_logger(__name__)


class PasswordPolicyError(ValueError):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-time check; a missing hash (SSO-only or unknown user) still costs a hash."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def check_policy(password: str, *, email: str | None = None) -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"Use at most {MAX_LENGTH} characters.")
    if email and password.strip().lower() == email.strip().lower():
        raise PasswordPolicyError("Don't use your email address as your password.")


async def is_breached(password: str, client: httpx.AsyncClient | None = None) -> bool:
    """Have I Been Pwned range check (k-anonymity: only 5 hex chars of the SHA-1 leave).

    Fails open: if the service can't be reached we don't block the user.
    """
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=3.0)
    try:
        response = await http.get(
            f"https://api.pwnedpasswords.com/range/{prefix}", headers={"Add-Padding": "true"}
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("breached-password check unavailable", error=type(exc).__name__)
        return False
    finally:
        if owns_client:
            await http.aclose()
    for line in response.text.splitlines():
        candidate, _, count = line.partition(":")
        if candidate == suffix and count.strip() != "0":
            return True
    return False
