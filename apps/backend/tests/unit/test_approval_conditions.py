# SPDX-License-Identifier: AGPL-3.0-only
"""The whitelisted condition language of approval policies."""

from decimal import Decimal
from typing import Any

import pytest

from pickwise.platform.approvals.conditions import ConditionError, evaluate, validate
from pickwise.platform.approvals.policy import StepSpec

ATTRS: dict[str, Any] = {
    "days": 6,
    "amount": "150000.50",
    "kind": "casual",
    "paid": True,
    "start": "2026-10-05",
    "dept": {"code": "ENG", "size": 12},
}


def holds(conditions: dict[str, Any], attributes: dict[str, Any] = ATTRS) -> bool:
    validate(conditions)
    return evaluate(conditions, attributes)


def leaf(field: str, op: str, value: Any) -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


def test_empty_conditions_match_everything() -> None:
    assert holds({}, {})


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (leaf("days", "eq", 6), True),
        (leaf("days", "eq", "6"), True),  # numeric string compares as a number
        (leaf("days", "ne", 6), False),
        (leaf("days", "gte", 6), True),
        (leaf("days", "gte", 7), False),
        (leaf("days", "lte", 6), True),
        (leaf("days", "between", [5, 10]), True),
        (leaf("days", "between", [7, 10]), False),
        (leaf("amount", "gte", "150000"), True),  # money as strings, never floats
        (leaf("amount", "lte", Decimal("150000.49")), False),
        (leaf("kind", "in", ["casual", "sick"]), True),
        (leaf("kind", "in", ["sick"]), False),
        (leaf("kind", "eq", "casual"), True),
        (leaf("paid", "eq", True), True),
        (leaf("paid", "eq", 1), False),  # a boolean is not the number 1
        (leaf("start", "gte", "2026-10-01"), True),  # ISO dates compare as strings
        (leaf("start", "lte", "2026-09-30"), False),
        (leaf("dept.code", "eq", "ENG"), True),  # dotted path into nested attributes
        (leaf("dept.size", "gte", 10), True),
        (leaf("dept.missing", "eq", 1), False),
        (leaf("absent", "ne", 1), False),  # a missing attribute never matches, even for "ne"
        (leaf("kind", "gte", 3), False),  # mixed kinds never order
        (leaf("days", "gte", "2026-01-01"), False),
    ],
)
def test_comparisons(condition: dict[str, Any], expected: bool) -> None:
    assert holds(condition) is expected


def test_all_and_any_nest() -> None:
    long_casual = {"all": [leaf("days", "gte", 5), leaf("kind", "eq", "casual")]}
    assert holds(long_casual)
    assert not holds({"all": [leaf("days", "gte", 5), leaf("kind", "eq", "sick")]})
    assert holds({"any": [leaf("kind", "eq", "sick"), leaf("days", "gte", 5)]})
    assert not holds({"any": [leaf("kind", "eq", "sick"), leaf("days", "gte", 50)]})
    assert holds({"any": [long_casual, leaf("kind", "eq", "sick")]})


@pytest.mark.parametrize(
    "bad",
    [
        [],
        "days >= 5",
        {"field": "days"},
        {"field": "days", "op": "regex", "value": "x"},
        {"field": "days", "op": "in", "value": 5},
        {"field": "days", "op": "in", "value": []},
        {"field": "days", "op": "between", "value": [1]},
        {"field": "days", "op": "between", "value": [1, "a"]},
        {"field": "days", "op": "gte", "value": [1]},
        {"field": "days", "op": "gte", "value": None},
        {"field": "", "op": "eq", "value": 1},
        {"all": []},
        {"all": "x"},
        {"all": [{}]},  # an empty condition only makes sense at the top
        {"all": [leaf("a", "eq", 1)], "any": [leaf("b", "eq", 1)]},
        {"field": "a", "op": "eq", "value": 1, "extra": 1},
        {"eval": "__import__('os')"},
    ],
)
def test_malformed_conditions_are_rejected(bad: Any) -> None:
    with pytest.raises(ConditionError):
        validate(bad)


def test_nesting_and_size_are_bounded() -> None:
    deep: dict[str, Any] = leaf("a", "eq", 1)
    for _ in range(6):
        deep = {"all": [deep]}
    with pytest.raises(ConditionError, match="too deeply"):
        validate(deep)
    wide = {"any": [leaf("a", "eq", i) for i in range(60)]}
    with pytest.raises(ConditionError, match="too many"):
        validate(wide)


def test_step_specs_check_approver_syntax_and_deduplicate() -> None:
    step = StepSpec(name="Manager", approvers=["manager", "role:hr_admin", "manager"])
    assert step.approvers == ["manager", "role:hr_admin"]
    for bad in ("Manager", "role:", "role:HR Admin", "user:123", "boss", "role:a b"):
        with pytest.raises(ValueError, match="approver"):
            StepSpec(name="x", approvers=[bad])
    with pytest.raises(ValueError, match="approver"):
        StepSpec(name="x", approvers=["manager"], fallback="nobody")
    with pytest.raises(ValueError, match="at least 1 item"):
        StepSpec(name="x", approvers=[])
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        StepSpec(name="x", approvers=["manager"], escalate_after_hours=0)
