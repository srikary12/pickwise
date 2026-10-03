# SPDX-License-Identifier: AGPL-3.0-only
"""Errors the API turns into a consistent JSON body: {"error": {"code", "message"}}.

Messages are user-facing and must never contain personal data or secrets.
"""

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        headers: dict[str, str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.headers = headers or {}
        self.details = details or {}


class NotAuthenticatedError(AppError):
    status_code = 401
    code = "not_authenticated"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class UnprocessableError(AppError):
    status_code = 422
    code = "invalid"


class TooManyRequestsError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "Too many attempts. Try again later.",
            headers={"Retry-After": str(max(1, retry_after_seconds))},
        )
