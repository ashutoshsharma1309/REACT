"""Structured (JSON) logging. Every log line is a single JSON object with an `event` field.

Event families used across REACT:
  simulation.*   detection.*   incident.created   risk.created   opportunity.created
  api.request    error
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

_CONFIGURED = False

# Never log secrets. Keys that look like credentials are masked; connection strings lose their password.
_SENSITIVE_KEY = re.compile(r"(password|passwd|secret|api[_-]?key|authorization|access[_-]?key|session[_-]?token|auth[_-]?token|credentials?|private[_-]?key)$", re.I)
_URL_PASSWORD = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<pw>[^@/\s]+)@", re.I)
REDACTED = "[REDACTED]"


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, str):
        return _URL_PASSWORD.sub(lambda m: f"{m['scheme']}{m['user']}:{REDACTED}@", value)
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(redact(extra))
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    root = logging.getLogger()
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.handlers = [handler]
        _CONFIGURED = True
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a structured event: log_event(log, "incident.created", incident_id=..., severity=...)."""
    logger.log(level, event, extra={"event": event, "extra_fields": fields})
