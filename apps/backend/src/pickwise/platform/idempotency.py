# SPDX-License-Identifier: AGPL-3.0-only
"""Idempotency-Key support for tenant POSTs that create something (CLAUDE.md rule 11).

Keys are stored per (tenant_id, principal_id, key) in ``platform.idempotency_keys``,
inside the request's own transaction:

- first use: the row is inserted, the handler runs, and its response is stored;
- same key + same body: the stored response is replayed and nothing runs again;
- same key + different body: 422;
- a concurrent duplicate waits on the row's unique index until the first request
  finishes, then replays (or proceeds, if the first one rolled back).

Pre-tenant endpoints don't accept the header; rate limits and single-use tokens
protect them instead.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.rbac.principal import Principal
from pickwise.shared.errors import UnprocessableError

HEADER = "Idempotency-Key"
MAX_KEY_LENGTH = 255


@dataclass(frozen=True, slots=True)
class Replay:
    status_code: int
    body: Any


class IdempotentRequest:
    def __init__(
        self, db: AsyncSession, principal: Principal, key: str | None, request_hash: bytes
    ) -> None:
        self._db = db
        self._principal = principal
        self._key = key
        self._hash = request_hash

    async def begin(self) -> Replay | None:
        """Claim the key, or return the stored response of an earlier identical request."""
        if self._key is None:
            return None
        inserted = (
            await self._db.execute(
                text(
                    "INSERT INTO platform.idempotency_keys (user_id, key, request_hash) "
                    "VALUES (:u, :k, :h) ON CONFLICT DO NOTHING RETURNING key"
                ),
                {"u": self._principal.principal_id, "k": self._key, "h": self._hash},
            )
        ).first()
        if inserted is not None:
            return None
        row = (
            await self._db.execute(
                text(
                    "SELECT request_hash, response_status, response_body FROM "
                    "platform.idempotency_keys "
                    "WHERE user_id = :u AND key = :k AND expires_at > now()"
                ),
                {"u": self._principal.principal_id, "k": self._key},
            )
        ).one_or_none()
        if row is None:
            # Expired: start over under the same key.
            await self._db.execute(
                text(
                    "UPDATE platform.idempotency_keys SET request_hash = :h, response_status = "
                    "NULL, "
                    "response_body = NULL, created_at = now(), expires_at = now() + interval '24 "
                    "hours' "
                    "WHERE user_id = :u AND key = :k"
                ),
                {"u": self._principal.principal_id, "k": self._key, "h": self._hash},
            )
            return None
        if bytes(row.request_hash) != self._hash:
            raise UnprocessableError(
                "This Idempotency-Key was already used for a different request.",
                code="idempotency_mismatch",
            )
        return Replay(int(row.response_status or 200), row.response_body)

    async def finish(self, status_code: int, body: Any) -> None:
        """Store the response to replay. ``body`` must not contain secrets."""
        if self._key is None:
            return
        await self._db.execute(
            text(
                "UPDATE platform.idempotency_keys SET response_status = :s, response_body = :b "
                "WHERE user_id = :u AND key = :k"
            ).bindparams(bindparam("b", type_=JSONB)),
            {"s": status_code, "b": body, "u": self._principal.principal_id, "k": self._key},
        )


async def idempotent(request: Request, db: AsyncSession, principal: Principal) -> IdempotentRequest:
    key = request.headers.get(HEADER)
    if key is not None and not (0 < len(key) <= MAX_KEY_LENGTH):
        raise UnprocessableError(
            f"{HEADER} must be 1-{MAX_KEY_LENGTH} characters.", code="invalid_idempotency_key"
        )
    body = await request.body()
    digest = hashlib.sha256(
        json.dumps([request.method, request.url.path], separators=(",", ":")).encode()
        + b"\n"
        + body
    ).digest()
    return IdempotentRequest(db, principal, key, digest)
