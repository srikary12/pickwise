# SPDX-License-Identifier: AGPL-3.0-only
"""The authenticated caller of a request."""

import uuid
from dataclasses import dataclass

from pickwise.shared.context import ActorType


@dataclass(frozen=True, slots=True)
class Principal:
    """Exactly one of (user_id + membership_id) or api_key_id identifies the caller.

    ``tenant_id`` is None for a signed-in user who hasn't picked a tenant yet.
    """

    actor_type: ActorType
    tenant_id: uuid.UUID | None
    user_id: uuid.UUID | None = None
    membership_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    # API keys carry their own permission list; users get theirs from roles.
    api_key_scopes: tuple[str, ...] = ()

    @property
    def principal_id(self) -> uuid.UUID:
        """The id idempotency keys and audit rows attribute actions to."""
        value = self.user_id or self.api_key_id
        if value is None:
            raise ValueError("principal has neither a user nor an API key")
        return value
