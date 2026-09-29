# SPDX-License-Identifier: AGPL-3.0-only
"""TOTP multi-factor authentication and recovery codes.

- The TOTP seed is encrypted with the platform data key, bound to the user id (AAD).
- A code is accepted once: ``users.mfa_last_used_step`` rejects replays.
- Recovery codes have 80 bits of entropy each, so a SHA-256 of (user id, code) is
  enough; they're single use and shown only when generated.
"""

import base64
import hashlib
import secrets
import time
import uuid

import pyotp

from pickwise.platform.crypto import Keyring

ISSUER = "Pickwise"
STEP_SECONDS = 30
RECOVERY_CODE_COUNT = 10


def new_secret() -> str:
    return pyotp.random_base32()


def secret_aad(user_id: uuid.UUID) -> bytes:
    return f"users.mfa_totp_secret_enc:{user_id}".encode()


def encrypt_secret(keyring: Keyring, user_id: uuid.UUID, secret: str) -> bytes:
    return keyring.encrypt(secret.encode(), secret_aad(user_id))


def decrypt_secret(keyring: Keyring, user_id: uuid.UUID, ciphertext: bytes) -> str:
    return keyring.decrypt(ciphertext, secret_aad(user_id)).decode()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def matching_step(
    secret: str, code: str, *, last_used_step: int | None, now: float | None = None
) -> int | None:
    """The time step ``code`` is valid for (±1 step of clock drift), if it wasn't used before."""
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret)
    current = int((now if now is not None else time.time()) // STEP_SECONDS)
    for step in (current - 1, current, current + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if secrets.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = base64.b32encode(secrets.token_bytes(10)).decode().lower()
        codes.append("-".join(raw[i : i + 4] for i in range(0, 16, 4)))
    return codes


def recovery_code_hash(user_id: uuid.UUID, code: str) -> bytes:
    normalised = code.strip().lower().replace(" ", "").replace("-", "")
    return hashlib.sha256(f"{user_id}:{normalised}".encode()).digest()
