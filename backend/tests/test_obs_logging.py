"""Structured JSON logging + run-id correlation, and text-default parity (M6)."""

from __future__ import annotations

import io
import json
import logging

from atlas.core.config import Settings
from atlas.core.logging import (
    JsonFormatter,
    bind_run_id,
    bind_task_id,
    configure_logging,
    reset_run_id,
    reset_task_id,
)


def _capture(fmt: str) -> io.StringIO:
    configure_logging("INFO", fmt)
    buffer = io.StringIO()
    logging.getLogger().handlers[0].stream = buffer  # type: ignore[attr-defined]
    return buffer


def test_json_format_is_valid_json_with_fields() -> None:
    buffer = _capture("json")
    logging.getLogger("atlas.test").warning("something %s", "happened")
    obj = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert obj["message"] == "something happened"
    assert obj["level"] == "WARNING"
    assert obj["logger"] == "atlas.test"
    assert "ts" in obj
    # No run bound → no correlation keys leak in.
    assert "run_id" not in obj


def test_json_format_includes_correlation_ids() -> None:
    buffer = _capture("json")
    rtok = bind_run_id("run-abc")
    ttok = bind_task_id("run-abc:0")
    try:
        logging.getLogger("atlas.test").info("in a run")
    finally:
        reset_task_id(ttok)
        reset_run_id(rtok)
    obj = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert obj["run_id"] == "run-abc"
    assert obj["task_id"] == "run-abc:0"


def test_correlation_resets_after_run() -> None:
    buffer = _capture("json")
    tok = bind_run_id("run-xyz")
    reset_run_id(tok)
    logging.getLogger("atlas.test").info("after reset")
    obj = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert "run_id" not in obj


def test_text_format_is_default_and_unchanged() -> None:
    buffer = _capture("text")
    logging.getLogger("atlas.test").info("plain line")
    line = buffer.getvalue().strip().splitlines()[-1]
    # The classic pipe-delimited text format, not JSON.
    assert " | INFO" in line
    assert "atlas.test" in line
    assert not line.lstrip().startswith("{")


def test_unknown_format_falls_back_to_text() -> None:
    buffer = _capture("bogus")
    assert not isinstance(
        logging.getLogger().handlers[0].formatter, JsonFormatter
    )
    logging.getLogger("atlas.test").info("fallback")
    assert " | INFO" in buffer.getvalue()


def test_config_defaults_preserve_m5_behavior() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.metrics_enabled is False
    assert s.log_format == "text"


def test_config_reads_obs_env(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ATLAS_METRICS_ENABLED", "true")
    monkeypatch.setenv("ATLAS_LOG_FORMAT", "json")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.metrics_enabled is True
    assert s.log_format == "json"


def _teardown_module() -> None:  # pragma: no cover - restore text logging
    configure_logging("WARNING", "text")
