"""Structured logging configuration (JSON or text format)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from .config import LoggingConfig


class JSONFormatter(logging.Formatter):
    """Emits log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge structured extra fields
        for key in ("service", "backend", "transaction_id", "elapsed_seconds",
                     "total_instances", "filtered"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val

        if record.exc_info and record.exc_info[1]:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable format for development."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def configure_logging(config: LoggingConfig) -> None:
    """Set up the root logger based on configuration."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.level.upper(), logging.INFO))

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    if config.format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(TextFormatter())

    root.addHandler(handler)

    # Suppress noisy loggers
    for noisy in ("azure", "urllib3", "msrest", "msal", "azure.identity", "azure.core"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
