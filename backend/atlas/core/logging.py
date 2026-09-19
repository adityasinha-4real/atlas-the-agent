"""Logging setup: a text formatter (default) or an opt-in JSON formatter.

A single ``configure_logging`` call installs a consistent formatter and a filter
that stamps the active ``run_id`` (and ``task_id``) onto every record via a
context variable (RFC-0004 §30). Modules obtain loggers via
``logging.getLogger(__name__)`` as usual. The default (text) format is unchanged
from M5, so existing behavior is byte-for-byte preserved (invariant I-23);
``ATLAS_LOG_FORMAT=json`` is a pure, opt-in addition.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime

_TEXT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

# Correlation context: bound around a run's execution so any log line emitted
# while a run is in flight is attributable without threading ids through calls.
_run_id_var: ContextVar[str | None] = ContextVar("atlas_run_id", default=None)
_task_id_var: ContextVar[str | None] = ContextVar("atlas_task_id", default=None)

_current_format = "text"


def bind_run_id(run_id: str | None) -> Token[str | None]:
    """Bind the active run id for log correlation. Returns a reset token."""
    return _run_id_var.set(run_id)


def reset_run_id(token: Token[str | None]) -> None:
    _run_id_var.reset(token)


def bind_task_id(task_id: str | None) -> Token[str | None]:
    return _task_id_var.set(task_id)


def reset_task_id(token: Token[str | None]) -> None:
    _task_id_var.reset(token)


class _CorrelationFilter(logging.Filter):
    """Attach ``run_id``/``task_id`` from the context to each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get()
        record.task_id = _task_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One compact JSON object per line, with correlation ids when present.

    Never emits full model I/O or secrets — it renders only the already-composed
    log message (redaction remains the caller's responsibility, RFC-0004 §26/§30).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        run_id = getattr(record, "run_id", None)
        if run_id:
            payload["run_id"] = run_id
        task_id = getattr(record, "task_id", None)
        if task_id:
            payload["task_id"] = task_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Configure the root logger. Safe (and cheap) to call repeatedly.

    ``fmt`` is ``"text"`` (default, unchanged) or ``"json"``. Re-configuring with
    a different format re-installs the handler, so tests and a runtime toggle both
    take effect deterministically.
    """
    global _current_format
    fmt = fmt.lower()
    if fmt not in ("text", "json"):
        fmt = "text"

    formatter: logging.Formatter = (
        JsonFormatter() if fmt == "json" else logging.Formatter(_TEXT_FORMAT)
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(_CorrelationFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn access logs are noisy; let our handler own formatting.
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).propagate = False

    _current_format = fmt
