# SPDX-License-Identifier: AGPL-3.0-only
"""Deferring background jobs without importing the worker.

Platform code can't import ``pickwise.worker`` (a composition root), so the API and
worker install a queue function at startup; ``defer`` then enqueues by task name.
Arguments must be ids only (CLAUDE.md rule 14). Call ``defer`` only after the
transaction that created the job's data has committed.
"""

from collections.abc import Awaitable, Callable

JobQueue = Callable[..., Awaitable[None]]
_queue: JobQueue | None = None


class JobQueueUnavailableError(RuntimeError):
    pass


def set_job_queue(queue: JobQueue | None) -> None:
    global _queue
    _queue = queue


async def defer(task_name: str, **kwargs: str | None) -> None:
    if _queue is None:
        raise JobQueueUnavailableError(f"no job queue installed; can't defer {task_name}")
    await _queue(task_name, **kwargs)
