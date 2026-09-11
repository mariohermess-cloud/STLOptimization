"""Structured JSON logging.

Every log record is emitted as a single JSON object so that a log shipper can
index ``model_id``, ``operation`` and ``duration_ms`` without regex parsing.

Rule: never log file contents or user supplied filenames verbatim beyond a
sanitised, length limited form (see :mod:`app.storage.upload`).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.config import settings

_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    # uvicorn brings its own handlers - route them through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False


@contextmanager
def timed(logger: logging.Logger, operation: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Log the duration of an operation, success or failure.

    The yielded dict can be mutated to attach extra fields to the final record.
    """
    extra: dict[str, Any] = {}
    start = time.perf_counter()
    try:
        yield extra
    except Exception:
        duration = (time.perf_counter() - start) * 1000.0
        logger.exception(
            "operation failed",
            extra={"operation": operation, "duration_ms": round(duration, 2), **fields, **extra},
        )
        raise
    duration = (time.perf_counter() - start) * 1000.0
    logger.info(
        "operation completed",
        extra={"operation": operation, "duration_ms": round(duration, 2), **fields, **extra},
    )
