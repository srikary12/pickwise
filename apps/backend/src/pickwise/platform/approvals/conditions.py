# SPDX-License-Identifier: AGPL-3.0-only
"""The condition language of approval policies.

A policy's ``conditions`` decide which requests it applies to. They are data, so they
can come from a tenant admin: this is a small whitelisted evaluator, never ``eval``.

    {}                                                  matches every request
    {"field": "days", "op": "gte", "value": 5}          a comparison on one attribute
    {"all": [<condition>, ...]}                         every condition holds
    {"any": [<condition>, ...]}                         at least one holds

Operators: ``eq``, ``ne``, ``in`` (value is a list), ``gte``, ``lte``, ``between``
(value is ``[low, high]``, inclusive). ``field`` may be a dotted path into nested
attributes. A condition on an attribute the request doesn't have is false, so an
incomplete request never matches by accident. Ordering operators compare numbers with
numbers (or numeric strings such as "1500.00") and other strings (ISO dates) with
strings; anything else is false.
"""

import re
from decimal import Decimal
from typing import Any

MAX_DEPTH = 5
MAX_NODES = 50
OPERATORS = frozenset({"eq", "ne", "in", "gte", "lte", "between"})
_MISSING = object()
# Money and other decimals travel as strings in JSON ("1500.00"), never as floats.
_NUMERIC_STRING = re.compile(r"^-?\d+(\.\d+)?$")


class ConditionError(ValueError):
    """The conditions document isn't valid. The message names the offending part."""


def validate(conditions: Any) -> None:
    nodes = 0

    def check(node: Any, depth: int, path: str) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES:
            raise ConditionError(f"too many conditions (at most {MAX_NODES})")
        if depth > MAX_DEPTH:
            raise ConditionError(f"{path}: nested too deeply (at most {MAX_DEPTH} levels)")
        if not isinstance(node, dict):
            raise ConditionError(f"{path}: must be an object")
        if not node:
            if depth > 1:
                raise ConditionError(f"{path}: an empty condition is only allowed at the top")
            return
        keys = set(node)
        if keys in ({"all"}, {"any"}):
            (key,) = keys
            children = node[key]
            if not isinstance(children, list) or not children:
                raise ConditionError(f"{path}.{key}: must be a non-empty list")
            for i, child in enumerate(children):
                check(child, depth + 1, f"{path}.{key}[{i}]")
            return
        if keys == {"field", "op", "value"}:
            if not isinstance(node["field"], str) or not node["field"]:
                raise ConditionError(f"{path}.field: must be a non-empty string")
            if node["op"] not in OPERATORS:
                raise ConditionError(f"{path}.op: must be one of {sorted(OPERATORS)}")
            value = node["value"]
            if node["op"] == "in" and not (isinstance(value, list) and value):
                raise ConditionError(f"{path}.value: 'in' needs a non-empty list")
            if node["op"] == "between" and not (
                isinstance(value, list)
                and len(value) == 2
                and all(_orderable(v) for v in value)
                and _comparable(value[0], value[1])
            ):
                raise ConditionError(f"{path}.value: 'between' needs [low, high] of one kind")
            if node["op"] in ("gte", "lte") and not _orderable(value):
                raise ConditionError(f"{path}.value: must be a number or a string")
            return
        raise ConditionError(
            f"{path}: use {{'all': [...]}}, {{'any': [...]}} or {{'field', 'op', 'value'}}"
        )

    check(conditions, 1, "conditions")


def evaluate(conditions: dict[str, Any], attributes: dict[str, Any]) -> bool:
    """Whether ``attributes`` satisfy ``conditions`` (assumed validated)."""
    if not conditions:
        return True
    if "all" in conditions:
        return all(evaluate(c, attributes) for c in conditions["all"])
    if "any" in conditions:
        return any(evaluate(c, attributes) for c in conditions["any"])
    actual = _lookup(attributes, conditions["field"])
    if actual is _MISSING:
        return False
    return _compare(conditions["op"], actual, conditions["value"])


def _lookup(attributes: dict[str, Any], path: str) -> Any:
    current: Any = attributes
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | Decimal):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str) and _NUMERIC_STRING.match(value):
        return Decimal(value)
    return None


def _orderable(value: Any) -> bool:
    return _number(value) is not None or isinstance(value, str)


def _comparable(a: Any, b: Any) -> bool:
    return (_number(a) is not None and _number(b) is not None) or (
        isinstance(a, str) and isinstance(b, str)
    )


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    na, nb = _number(a), _number(b)
    if na is not None and nb is not None:
        return na == nb
    return bool(a == b)


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return _equal(actual, expected)
    if op == "ne":
        return not _equal(actual, expected)
    if op == "in":
        return any(_equal(actual, e) for e in expected)
    if op == "between":
        return _compare("gte", actual, expected[0]) and _compare("lte", actual, expected[1])
    if not _comparable(actual, expected):
        return False
    if isinstance(expected, str):
        return bool(actual >= expected if op == "gte" else actual <= expected)
    a, e = _number(actual), _number(expected)
    assert a is not None
    assert e is not None
    return a >= e if op == "gte" else a <= e
