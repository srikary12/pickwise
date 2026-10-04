# SPDX-License-Identifier: AGPL-3.0-only
"""Approval policies: which requests they match and who approves, step by step."""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# role:<key> and user:<id> are resolved by platform; the rest by core (Phase 5).
APPROVER_PATTERN = re.compile(
    r"^(role:[a-z][a-z0-9_]{1,62}"
    r"|user:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|manager|skip_level|dept_head)$"
)
MAX_STEPS = 10
MAX_APPROVERS_PER_STEP = 10
MAX_ESCALATE_HOURS = 24 * 30


def check_approver(spec: str) -> str:
    if not APPROVER_PATTERN.match(spec):
        raise ValueError(
            f"{spec!r} isn't an approver: "
            "use role:<key>, user:<id>, manager, skip_level or dept_head"
        )
    return spec


def split_approver(spec: str) -> tuple[str, str | None]:
    kind, _, argument = spec.partition(":")
    return kind, argument or None


class StepSpec(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    approvers: list[str] = Field(min_length=1, max_length=MAX_APPROVERS_PER_STEP)
    mode: Literal["any", "all"] = Field(
        default="any", description="'any': one approver decides the step. 'all': everyone must."
    )
    escalate_after_hours: int | None = Field(
        default=None,
        ge=1,
        le=MAX_ESCALATE_HOURS,
        description="Hand an unanswered task to the fallback after this long; a reminder goes "
        "out at half the time. Omit for no escalation.",
    )
    fallback: str | None = Field(
        default=None,
        description="Who gets an escalated task. Default: the approver's manager (skip_level), "
        "else the tenant_admin role.",
    )
    allow_self_approval: bool = Field(
        default=False, description="Let the requester approve their own request at this step."
    )

    @field_validator("approvers")
    @classmethod
    def _approvers(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(check_approver(v) for v in values))

    @field_validator("fallback")
    @classmethod
    def _fallback(cls, value: str | None) -> str | None:
        return None if value is None else check_approver(value)


def parse_steps(raw: Any) -> list[StepSpec]:
    return [StepSpec.model_validate(s) for s in raw]
