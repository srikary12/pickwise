# SPDX-License-Identifier: AGPL-3.0-only
import base64
import os

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pickwise.platform.crypto import (
    BLIND_INDEX,
    DATA,
    CiphertextError,
    DataKey,
    EnvKek,
    Keyring,
    blind_index,
    decrypt,
    encrypt,
    key_version_of,
)
from pickwise.platform.crypto.kek import KekUnavailableError

KEY_V1 = DataKey(1, os.urandom(32))
KEY_V2 = DataKey(2, os.urandom(32))


@given(st.binary(max_size=4096), st.binary(max_size=64))
def test_encrypt_round_trips(plaintext: bytes, aad: bytes) -> None:
    ciphertext = encrypt(KEY_V1, plaintext, aad)
    assert key_version_of(ciphertext) == 1
    assert decrypt({1: KEY_V1}, ciphertext, aad) == plaintext


def test_ciphertext_is_bound_to_its_aad() -> None:
    ciphertext = encrypt(KEY_V1, b"123456789012", b"users.mfa:alice")
    with pytest.raises(CiphertextError, match="authentication"):
        decrypt({1: KEY_V1}, ciphertext, b"users.mfa:bob")


def test_old_versions_stay_readable_after_rotation() -> None:
    old = encrypt(KEY_V1, b"legacy", b"aad")
    ring = Keyring({DATA: {1: KEY_V1, 2: KEY_V2}}, {DATA: 2})
    new = ring.encrypt(b"fresh", b"aad")
    assert key_version_of(new) == 2
    assert ring.decrypt(old, b"aad") == b"legacy"
    assert ring.decrypt(new, b"aad") == b"fresh"


def test_unknown_version_and_garbage_are_rejected() -> None:
    with pytest.raises(CiphertextError, match="no key"):
        decrypt({1: KEY_V1}, encrypt(KEY_V2, b"x", b""), b"")
    with pytest.raises(CiphertextError):
        decrypt({1: KEY_V1}, b"\x09garbage-that-is-long-enough-to-parse", b"")


def test_key_material_never_appears_in_repr() -> None:
    assert KEY_V1.material.hex() not in repr(KEY_V1)
    assert "material" not in repr(KEY_V1)


def test_env_kek_wraps_and_unwraps() -> None:
    kek = EnvKek.from_base64(base64.b64encode(os.urandom(32)).decode())
    wrapped = kek.wrap(KEY_V1.material, b"tenant_keys:t:data:1")
    assert kek.unwrap(wrapped, b"tenant_keys:t:data:1") == KEY_V1.material
    assert kek.kek_id.startswith("env:")
    with pytest.raises(Exception):  # noqa: B017, PT011 - any failure; cryptography raises InvalidTag
        kek.unwrap(wrapped, b"tenant_keys:other:data:1")


@pytest.mark.parametrize("value", ["", "not base64!!", base64.b64encode(b"short").decode()])
def test_env_kek_rejects_bad_material(value: str) -> None:
    with pytest.raises(KekUnavailableError):
        EnvKek.from_base64(value)


def test_blind_index_is_deterministic_and_key_specific() -> None:
    k1, k2 = os.urandom(32), os.urandom(32)
    assert blind_index(k1, "ABCDE1234F") == blind_index(k1, "ABCDE1234F")
    assert blind_index(k1, "ABCDE1234F") != blind_index(k2, "ABCDE1234F")
    ring = Keyring({BLIND_INDEX: {1: DataKey(1, k1), 2: DataKey(2, k2)}}, {BLIND_INDEX: 2})
    assert set(ring.blind_indexes("ABCDE1234F")) == {
        blind_index(k1, "ABCDE1234F"),
        blind_index(k2, "ABCDE1234F"),
    }
