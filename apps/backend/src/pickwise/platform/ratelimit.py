# SPDX-License-Identifier: AGPL-3.0-only
"""Token-bucket rate limiting in Postgres, shared by every API replica (ADR 0011).

A bucket is keyed by HMAC(SESSION_SECRET, "<rule>:<subject>"), so the table never
holds a raw IP address or email. One atomic upsert refills and spends a token.
"""

import hashlib
import hmac
import math
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.shared.errors import TooManyRequestsError
from pickwise.shared.settings import Settings


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    capacity: int
    per_seconds: int

    @property
    def refill_per_second(self) -> float:
        return self.capacity / self.per_seconds


LOGIN_PER_IP = Rule("login:ip", 20, 60)
LOGIN_PER_EMAIL = Rule("login:email", 5, 60)
MFA_PER_USER = Rule("mfa:user", 5, 60)
SIGNUP_PER_IP = Rule("signup:ip", 5, 3600)
SIGNUP_PER_EMAIL = Rule("signup:email", 3, 3600)
RESET_PER_EMAIL = Rule("reset:email", 3, 3600)
RESET_PER_IP = Rule("reset:ip", 20, 3600)
LOOKUP_PER_IP = Rule("lookup:ip", 60, 60)
SEARCH_PER_USER = Rule("search:user", 120, 60)
REVEAL_PER_USER = Rule("reveal:user", 30, 60)

_TAKE = text(
    """
    INSERT INTO platform.rate_limit_buckets AS b (key_hash, tokens, refilled_at)
    VALUES (:key, :capacity - 1, now())
    ON CONFLICT (key_hash) DO UPDATE SET
        -- Floor at -capacity: hammering keeps the bucket empty, but the wait
        -- never exceeds two refill periods.
        tokens = greatest(-:capacity,
                          least(:capacity,
                                b.tokens + extract(epoch FROM now() - b.refilled_at) * :rate) - 1),
        refilled_at = now()
    RETURNING tokens
    """
)


def bucket_key(settings: Settings, rule: Rule, subject: str) -> bytes:
    message = f"{rule.name}:{subject.strip().lower()}".encode()
    return hmac.new(
        settings.session_secret.get_secret_value().encode(), message, hashlib.sha256
    ).digest()


async def hit(session: AsyncSession, settings: Settings, rule: Rule, subject: str) -> None:
    """Spend one token; raise TooManyRequestsError (429 + Retry-After) when the bucket is empty.

    A denied request still spends (down to -capacity), so hammering keeps the bucket empty.
    """
    tokens: float = (
        await session.execute(
            _TAKE,
            {
                "key": bucket_key(settings, rule, subject),
                "capacity": float(rule.capacity),
                "rate": rule.refill_per_second,
            },
        )
    ).scalar_one()
    if tokens < 0:
        raise TooManyRequestsError(math.ceil(-tokens / rule.refill_per_second))


async def prune(session: AsyncSession, older_than_seconds: int = 86400) -> int:
    """Drop buckets idle long enough to be full again (ops session)."""
    result = await session.execute(
        text(
            "DELETE FROM platform.rate_limit_buckets "
            "WHERE refilled_at < now() - make_interval(secs => :s)"
        ),
        {"s": older_than_seconds},
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
