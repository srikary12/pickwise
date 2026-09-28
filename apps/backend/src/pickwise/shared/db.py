# SPDX-License-Identifier: AGPL-3.0-only
"""Database access: the only two ways to open a session (CLAUDE.md rules 3-4).

- ``tenant_session(ctx)``: every request and job. One transaction whose first
  statement sets app.tenant_id / user_id / actor_type / request_id / client_ip
  transaction-locally (safe behind PgBouncer). RLS does the rest.
- ``ops_session()``: cross-tenant platform work. It runs ``SET LOCAL ROLE
  pickwise_ops`` so the ``ops_all`` policies apply. Only functions decorated
  ``@ops_task`` (worker) or ``@ops_command`` (CLI) may call it; that's checked at
  runtime here and statically by an AST test.
"""

import contextvars
import functools
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, ParamSpec, TypeVar, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.settings import Settings

P = ParamSpec("P")
R = TypeVar("R")

_SET_CONTEXT = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), "
    "set_config('app.user_id', :user_id, true), "
    "set_config('app.actor_type', :actor_type, true), "
    "set_config('app.request_id', :request_id, true), "
    "set_config('app.client_ip', :client_ip, true)"
)

# Which kind of ops entry point is running, if any.
_ops_scope: contextvars.ContextVar[ActorType | None] = contextvars.ContextVar(
    "pickwise_ops_scope", default=None
)


class OpsSessionNotAllowedError(RuntimeError):
    """ops_session() was called outside an @ops_task / @ops_command function."""


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.sqlalchemy_url(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"server_settings": {"application_name": settings.database_user}},
    )


class Database:
    """One engine per process, for that process's login user."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        return cls(create_engine(settings))

    async def dispose(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def tenant_session(self, ctx: RequestContext) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session, session.begin():
            await session.execute(_SET_CONTEXT, _context_params(ctx.settings()))
            yield session

    @asynccontextmanager
    async def ops_session(self, request_id: str | None = None) -> AsyncIterator[AsyncSession]:
        scope = _ops_scope.get()
        if scope is None:
            raise OpsSessionNotAllowedError(
                "ops_session() may only be used inside @ops_task or @ops_command functions"
            )
        ctx = RequestContext(actor_type=scope, request_id=request_id)
        async with self._sessions() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE pickwise_ops"))
            await session.execute(_SET_CONTEXT, _context_params(ctx.settings()))
            yield session


def _context_params(values: dict[str, str]) -> dict[str, str]:
    return {key.removeprefix("app."): value for key, value in values.items()}


def _ops_entry_point(actor: ActorType) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def run_async(*args: P.args, **kwargs: P.kwargs) -> Any:
                token = _ops_scope.set(actor)
                try:
                    return await cast(Callable[P, Awaitable[Any]], func)(*args, **kwargs)
                finally:
                    _ops_scope.reset(token)

            return cast(Callable[P, R], run_async)

        @functools.wraps(func)
        def run_sync(*args: P.args, **kwargs: P.kwargs) -> R:
            token = _ops_scope.set(actor)
            try:
                return func(*args, **kwargs)
            finally:
                _ops_scope.reset(token)

        return run_sync

    return decorate


# A worker task that may open ops sessions (actor_type 'worker').
ops_task = _ops_entry_point(ActorType.WORKER)
# A CLI command, run as pickwise_maint, that may open ops sessions (actor_type 'system').
ops_command = _ops_entry_point(ActorType.SYSTEM)
