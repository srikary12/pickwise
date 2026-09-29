# SPDX-License-Identifier: AGPL-3.0-only
"""Who is acting, for which tenant: set on every database transaction (CLAUDE.md rule 3)."""

import uuid
from dataclasses import dataclass
from enum import StrEnum


class ActorType(StrEnum):
    USER = "user"
    API_KEY = "api_key"
    CANDIDATE = "candidate"
    SYSTEM = "system"
    WORKER = "worker"


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Values written with set_config(..., true) at the start of every transaction.

    ``tenant_id`` is None only for pre-tenant work (login, signup), which can read
    global tables and call the SECURITY DEFINER lookups but sees no tenant rows.
    """

    actor_type: ActorType
    tenant_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    request_id: str | None = None
    client_ip: str | None = None

    def settings(self) -> dict[str, str]:
        return {
            "app.tenant_id": str(self.tenant_id) if self.tenant_id else "",
            "app.user_id": str(self.user_id) if self.user_id else "",
            "app.actor_type": self.actor_type.value,
            "app.request_id": self.request_id or "",
            "app.client_ip": self.client_ip or "",
        }
