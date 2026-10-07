"""Structured application logging and cross-service correlation helpers."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from langchain_core.runnables import ensure_config

_safe_id = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_standard_record_fields = set(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}


class JsonLogFormatter(logging.Formatter):
    """Render one JSON object per line for container-friendly ingestion."""

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": self.service,
            "environment": self.environment,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _standard_record_fields and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    *,
    service: str,
    environment: str,
    namespace: str = "source",
    env: Mapping[str, str] | None = None,
) -> None:
    """Configure application-owned loggers without replacing server handlers."""
    values = os.environ if env is None else env
    logger = logging.getLogger(namespace)
    if any(getattr(handler, "_nexus_handler", False) for handler in logger.handlers):
        return

    level_name = str(values.get("NEXUS_LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler()
    handler._nexus_handler = True  # type: ignore[attr-defined]
    if str(values.get("NEXUS_LOG_FORMAT", "json")).lower() == "console":
        handler.setFormatter(
            logging.Formatter(
                f"%(asctime)s %(levelname)s [{service}] %(name)s: %(message)s"
            )
        )
    else:
        handler.setFormatter(JsonLogFormatter(service=service, environment=environment))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    """Emit a named event with JSON-safe, explicitly selected fields."""
    logger.log(level, event, extra={"event": event, **fields}, exc_info=exc_info)


def correlation_headers() -> dict[str, str]:
    """Build internal HTTP correlation headers from the active graph run."""
    config = ensure_config()
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    thread_id = configurable.get("thread_id") or metadata.get("thread_id")
    run_id = config.get("run_id") or metadata.get("run_id")
    headers = {"X-Request-ID": str(uuid4())}
    if _valid_id(thread_id):
        headers["X-Nexus-Thread-ID"] = str(thread_id)
    if _valid_id(run_id):
        headers["X-Nexus-Run-ID"] = str(run_id)
    return headers


def correlation_ids() -> tuple[str | None, str | None]:
    """Return validated graph thread/run identifiers for durable evidence links."""
    config = ensure_config()
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    thread_id = configurable.get("thread_id") or metadata.get("thread_id")
    run_id = config.get("run_id") or metadata.get("run_id")
    return (
        str(thread_id) if _valid_id(thread_id) else None,
        str(run_id) if _valid_id(run_id) else None,
    )


def _valid_id(value: object) -> bool:
    return value is not None and bool(_safe_id.fullmatch(str(value)))
