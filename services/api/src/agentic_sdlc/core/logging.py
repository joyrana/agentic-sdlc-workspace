"""Structured JSON logging with correlation IDs and secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-~+/]+=*"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b(\s*[=:]\s*)(['\"]?)[^\s'\",]+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def redact(text: str) -> str:
    """Remove credential-like material from free text."""
    out = text
    out = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", out)
    out = _SECRET_PATTERNS[1].sub(r"\1\2\3[REDACTED]", out)
    for pat in _SECRET_PATTERNS[2:]:
        out = pat.sub("[REDACTED]", out)
    return out


def redact_obj(value: Any) -> Any:
    """Recursively redact strings and drop values under secret-looking keys."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in value.items():
            if re.search(r"(?i)(secret|password|token|api[_-]?key|credential)", str(k)):
                result[k] = "[REDACTED]"
            else:
                result[k] = redact_obj(v)
        return result
    if isinstance(value, list | tuple):
        return [redact_obj(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "correlation_id": correlation_id_var.get(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(redact_obj(extra))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def log(logger: logging.Logger, level: int, msg: str, **fields: Any) -> None:
    logger.log(level, msg, extra={"fields": fields})
