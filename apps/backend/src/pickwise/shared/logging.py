# SPDX-License-Identifier: AGPL-3.0-only
"""structlog JSON logging.

Log ids only: never PII, resume text, salary figures or tokens (CLAUDE.md rule 14).
Request-scoped values (request_id) are bound through structlog contextvars.
"""

import json
import logging
import sys
from typing import Any

import structlog

# Put the fields people scan for first; everything else follows in call order.
_LEADING_KEYS = ("timestamp", "level", "logger", "event", "request_id")


def _leading_keys_first(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    ordered = {k: event_dict.pop(k) for k in _LEADING_KEYS if k in event_dict}
    ordered.update(event_dict)
    return ordered


def _dumps(obj: Any, **kwargs: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, **kwargs)


def configure_logging(level: str = "INFO") -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _leading_keys_first,
            structlog.processors.JSONRenderer(serializer=_dumps),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn's access log would print raw query strings; the API logs its own
    # access lines with request ids instead.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error", "procrastinate"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
