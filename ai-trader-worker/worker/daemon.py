"""Background market-intel refresh loop for the AI-Trader adapter."""

from __future__ import annotations

import logging
import os
import signal
import threading
from typing import Callable, Mapping

from pydantic import ValidationError

from worker.models import MarketRefreshRequest
from worker.observability import configure_logging, log_event
from worker.runtime import runtime_db_path
from worker.service import AiTraderWorker, WorkerConfigError

DEFAULT_REFRESH_INTERVAL_SECONDS = 3600
DEFAULT_STARTUP_DELAY_SECONDS = 3


def build_refresh_request(env: Mapping[str, str] | None = None) -> MarketRefreshRequest:
    values = os.environ if env is None else env
    return MarketRefreshRequest(
        categories=_env_csv(values, "AI_TRADER_MARKET_NEWS_CATEGORIES"),
        lookback_hours=_env_int_optional(values, "AI_TRADER_MARKET_NEWS_LOOKBACK_HOURS"),
        limit=_env_int_optional(values, "AI_TRADER_MARKET_NEWS_LIMIT"),
    )


def refresh_market_news_once(
    *,
    worker: AiTraderWorker | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    service = worker or AiTraderWorker(runtime_db_path(env))
    return service.refresh_market_intel(build_refresh_request(env))


def run_refresh_loop(
    *,
    env: Mapping[str, str] | None = None,
    stop_event: threading.Event | None = None,
    worker_factory: Callable[[], AiTraderWorker] | None = None,
    logger: logging.Logger | None = None,
) -> None:
    values = os.environ if env is None else env
    current_logger = logger or logging.getLogger(__name__)
    interval = _env_int(
        values,
        "AI_TRADER_MARKET_NEWS_REFRESH_INTERVAL_SECONDS",
        DEFAULT_REFRESH_INTERVAL_SECONDS,
        minimum=300,
    )
    startup_delay = _env_int(
        values,
        "AI_TRADER_MARKET_NEWS_REFRESH_STARTUP_DELAY_SECONDS",
        DEFAULT_STARTUP_DELAY_SECONDS,
        minimum=0,
    )
    stop = stop_event or threading.Event()
    factory = worker_factory or (lambda: AiTraderWorker(runtime_db_path(values)))

    if startup_delay and stop.wait(startup_delay):
        return

    while not stop.is_set():
        try:
            payload = refresh_market_news_once(worker=factory(), env=values)
            categories = payload.get("categories") if isinstance(payload, dict) else []
            errors = [
                str(item.get("category"))
                for item in categories
                if isinstance(item, dict) and item.get("error")
            ]
            log_event(
                current_logger,
                "market_intel.refresh.completed",
                refreshed_at=(
                    payload.get("refreshed_at") if isinstance(payload, dict) else None
                ),
                error_categories=errors,
            )
        except WorkerConfigError as exc:
            log_event(
                current_logger,
                "market_intel.refresh.skipped",
                level=logging.WARNING,
                reason=exc.message,
            )
        except ValidationError as exc:
            log_event(
                current_logger,
                "market_intel.refresh.invalid_config",
                level=logging.ERROR,
                error_type=type(exc).__name__,
            )
        except Exception:
            log_event(
                current_logger,
                "market_intel.refresh.failed",
                level=logging.ERROR,
                exc_info=True,
            )

        if stop.wait(interval):
            return


def main() -> None:
    log_env = os.environ
    if "NEXUS_LOG_LEVEL" not in log_env and "AI_TRADER_REFRESH_LOG_LEVEL" in log_env:
        log_env = dict(log_env)
        log_env["NEXUS_LOG_LEVEL"] = log_env["AI_TRADER_REFRESH_LOG_LEVEL"]
    configure_logging("ai-trader-refresh", env=log_env)
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()

    for signame in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, signame), request_stop)

    run_refresh_loop(stop_event=stop)


def _env_csv(values: Mapping[str, str], name: str) -> list[str] | None:
    raw = str(values.get(name, "") or "").strip()
    if not raw:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or None


def _env_int_optional(values: Mapping[str, str], name: str) -> int | None:
    raw = str(values.get(name, "") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _env_int(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
) -> int:
    raw = str(values.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(minimum, parsed)


if __name__ == "__main__":
    main()
