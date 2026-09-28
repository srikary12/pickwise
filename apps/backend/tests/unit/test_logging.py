# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging

import pytest
import structlog

from pickwise.shared.logging import configure_logging, get_logger


def test_json_lines_lead_with_timestamp_level_logger_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    structlog.contextvars.bind_contextvars(request_id="req-1")
    try:
        get_logger("pickwise.test").warning("STUB SCANNER — DEV ONLY", scanner="stub")
    finally:
        structlog.contextvars.clear_contextvars()
    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)
    assert list(record)[:5] == ["timestamp", "level", "logger", "event", "request_id"]
    assert record["scanner"] == "stub"
    assert "—" in line, "non-ASCII must not be escaped"
    logging.getLogger().handlers.clear()
