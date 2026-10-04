# SPDX-License-Identifier: AGPL-3.0-only
"""Client-side UUIDv7 (RFC 9562), for rows whose id must be known before the INSERT.

The database default is the native ``uuidv7()``; this is only for the few cases where
the application needs the id first (e.g. the AAD of an encrypted column).
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    millis = time.time_ns() // 1_000_000
    random = int.from_bytes(os.urandom(10), "big")
    value = (millis & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76  # version
    value |= ((random >> 68) & 0xFFF) << 64  # rand_a (12 bits)
    value |= 0b10 << 62  # variant
    value |= random & ((1 << 62) - 1)  # rand_b (62 bits)
    return uuid.UUID(int=value)
