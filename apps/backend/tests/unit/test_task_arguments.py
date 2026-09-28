# SPDX-License-Identifier: AGPL-3.0-only
"""Background-job arguments carry only ids and tenant_id (CLAUDE.md rule 14).

procrastinate's queue tables are global, so a PII or salary figure in a job's
arguments would be readable across tenants and would outlive erasure.
"""

import datetime
import enum
import inspect
import types
import typing
import uuid

from procrastinate.job_context import JobContext

import pickwise.worker.tasks  # noqa: F401 - registers the tasks
from pickwise.worker.app import app

ALLOWED_TYPES: tuple[type, ...] = (uuid.UUID, int, bool, datetime.date, datetime.datetime)
# A str is allowed only for opaque keys/codes such as an idempotency key or a leave-type code.
ALLOWED_STR_SUFFIXES = ("_key", "_code")


def _allowed(name: str, annotation: object) -> bool:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return all(arg is type(None) or _allowed(name, arg) for arg in typing.get_args(annotation))
    if annotation is str:
        return name.endswith(ALLOWED_STR_SUFFIXES)
    if isinstance(annotation, type):
        return issubclass(annotation, ALLOWED_TYPES) or issubclass(annotation, enum.Enum)
    return False


def test_registered_tasks_take_only_ids_and_keys() -> None:
    problems = []
    # procrastinate's own maintenance tasks ("builtin:…") aren't ours to police.
    ours = [t for t in app.tasks.values() if not t.name.startswith("builtin:")]
    assert ours, "no Pickwise tasks registered"
    for task in ours:
        hints = typing.get_type_hints(task.func)
        for name, param in inspect.signature(task.func).parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                problems.append(f"{task.name}: *{name} is not allowed")
                continue
            annotation = hints.get(name)
            if annotation is JobContext:  # pass_context=True tasks receive the job context
                continue
            if annotation is None or not _allowed(name, annotation):
                problems.append(f"{task.name}: argument {name!r} has type {annotation!r}")
    assert not problems, "\n".join(problems)


def test_the_check_rejects_free_text() -> None:
    assert not _allowed("candidate_name", str)
    assert not _allowed("salary", float)
    assert not _allowed("payload", dict)
    assert _allowed("idempotency_key", str)
    assert _allowed("tenant_id", uuid.UUID)
    assert _allowed("employee_id", uuid.UUID | None)
