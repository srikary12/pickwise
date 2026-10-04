# SPDX-License-Identifier: AGPL-3.0-only
import uuid

import pytest

from pickwise.platform.crypto import last4, mask_email, mask_phone, mask_tail, normalize_identifier
from pickwise.shared.ids import uuid7


def test_last4() -> None:
    assert last4("1234 5678 9012") == "9012"
    assert last4("ABCDE1234F") == "234F"
    assert last4("12") == "12"
    assert last4("") is None
    assert last4(None) is None


def test_masks() -> None:
    assert mask_tail("ABCDE1234F") == "••••••234F"
    assert mask_tail("123") == "•••"
    assert mask_email("asha.rao@example.test") == "a•••••••@example.test"
    assert mask_email("not an email") == "••••••••••"
    assert mask_phone("+91 98765 43210") == "••••••••••10"
    assert mask_phone("") == ""


def test_normalize_identifier() -> None:
    assert normalize_identifier(" abcde 1234-f ") == "ABCDE1234F"


def test_uuid7_is_version_7_and_time_ordered() -> None:
    ids = [uuid7() for _ in range(50)]
    assert all(i.version == 7 and i.variant == uuid.RFC_4122 for i in ids)
    assert len({*ids}) == 50
    assert [i.int >> 80 for i in ids] == sorted(i.int >> 80 for i in ids)


@pytest.mark.parametrize("value", ["", "  "])
def test_blank_inputs_are_safe(value: str) -> None:
    assert last4(value) is None
    assert mask_tail(value) == ""
