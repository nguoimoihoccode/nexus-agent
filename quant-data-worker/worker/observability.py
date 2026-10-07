"""Structured logging and HTTP correlation for the worker service."""

from __future__ import annotations

import json
import logging
import os
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Mapping
from uuid import uuid4

from fastapi import FastAPI, Request, Response

_context: ContextVar[dict[str, str]] = ContextVar("nexus_log_context", default={})
_safe_id = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_standard_fields = set(logging.makeLogRecord({}).__dict__) | {"asctime", "message"}


class JsonLogFormatter(logging.Formatter):
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
        payload.update(_context.get())
        for key, value in record.__dict__.items():
            if key not in _standard_fields and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    service: str,
    *,
    namespace: str = "worker",
    env: Mapping[str, str] | None = None,
) -> None:
    values = os.environ if env is None else env
    logger = logging.getLogger(namespace)
    if any(getattr(handler, "_nexus_handler", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler._nexus_handler = True  # type: ignore[attr-defined]
    environment = str(values.get("NEXUS_ENV", "development"))
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
    logger.setLevel(
        getattr(logging, str(values.get("NEXUS_LOG_LEVEL", "INFO")).upper(), logging.INFO)
    )
    logger.propagate = False


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    logger.log(level, event, extra={"event": event, **fields}, exc_info=exc_info)


def install_http_logging(app: FastAPI, *, service: str) -> None:
    logger = logging.getLogger(f"worker.http.{service}")

    @app.middleware("http")
    async def observe_request(request: Request, call_next) -> Response:
        request_id = _header_id(request, "X-Request-ID") or str(uuid4())
        context = {"request_id": request_id}
        thread_id = _header_id(request, "X-Nexus-Thread-ID")
        run_id = _header_id(request, "X-Nexus-Run-ID")
        if thread_id:
            context["thread_id"] = thread_id
        if run_id:
            context["run_id"] = run_id
        token = _context.set(context)
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log_event(
                logger,
                "http.request.failed",
                level=logging.ERROR,
                exc_info=True,
                method=request.method,
                path=request.url.path,
                duration_ms=round((perf_counter() - started_at) * 1000, 2),
            )
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            if request.url.path != "/health" or response.status_code >= 400:
                log_event(
                    logger,
                    "http.request.completed",
                    level=(logging.WARNING if response.status_code >= 400 else logging.INFO),
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=round((perf_counter() - started_at) * 1000, 2),
                )
            return response
        finally:
            _context.reset(token)


def _header_id(request: Request, name: str) -> str | None:
    value = request.headers.get(name, "").strip()
    return value if _safe_id.fullmatch(value) else None
