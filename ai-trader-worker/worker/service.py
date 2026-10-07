"""Compatibility facade composing AI-Trader application services and adapters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from worker.adapters import AlphaVantageNewsProvider, SQLiteAiTraderRepository
from worker.adapters.alpha_vantage import (
    ALPHA_VANTAGE_DEFAULT_BASE_URL,
    MarketNewsFetcher,
)
from worker.application import MarketIntelService, SignalService
from worker.domain.market import (
    CATEGORY_DEFINITIONS,
    MARKET_CATEGORIES,
    WorkerConfigError,
)
from worker.models import DiscussionRequest, MarketRefreshRequest, StrategyRequest


class AiTraderWorker:
    """Public facade for market-intel and signal use cases."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        alpha_vantage_api_key: str | None = None,
        alpha_vantage_base_url: str | None = None,
        market_news_lookback_hours: int | None = None,
        market_news_limit: int | None = None,
        market_news_fetcher: MarketNewsFetcher | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.alpha_vantage_api_key = _coalesce_env(
            alpha_vantage_api_key,
            "ALPHA_VANTAGE_API_KEY",
        )
        self.alpha_vantage_base_url = (
            _coalesce_env(alpha_vantage_base_url, "ALPHA_VANTAGE_BASE_URL")
            or ALPHA_VANTAGE_DEFAULT_BASE_URL
        )
        self.market_news_lookback_hours = _coalesce_int_env(
            market_news_lookback_hours,
            "AI_TRADER_MARKET_NEWS_LOOKBACK_HOURS",
            default=48,
            minimum=1,
            maximum=720,
        )
        self.market_news_limit = _coalesce_int_env(
            market_news_limit,
            "AI_TRADER_MARKET_NEWS_LIMIT",
            default=50,
            minimum=1,
            maximum=100,
        )
        self.market_news_refresh_interval_seconds = _coalesce_int_env(
            None,
            "AI_TRADER_MARKET_NEWS_REFRESH_INTERVAL_SECONDS",
            default=3600,
            minimum=300,
            maximum=86400,
        )
        self.market_news_stale_after_seconds = _coalesce_int_env(
            None,
            "AI_TRADER_MARKET_NEWS_STALE_AFTER_SECONDS",
            default=self.market_news_refresh_interval_seconds * 2,
            minimum=self.market_news_refresh_interval_seconds,
            maximum=604800,
        )
        self.market_news_fetcher = market_news_fetcher
        self.repository = SQLiteAiTraderRepository(self.db_path)
        self.market_intel_service = MarketIntelService(
            self.repository,
            AlphaVantageNewsProvider(
                self.alpha_vantage_base_url,
                fetcher=market_news_fetcher,
            ),
            api_key=self.alpha_vantage_api_key,
            lookback_hours=self.market_news_lookback_hours,
            news_limit=self.market_news_limit,
            refresh_interval_seconds=self.market_news_refresh_interval_seconds,
            stale_after_seconds=self.market_news_stale_after_seconds,
        )
        self.signal_service = SignalService(self.repository)

    def health(self) -> dict[str, str | bool]:
        return self.repository.health()

    def market_overview(self) -> dict[str, Any]:
        return self.market_intel_service.market_overview()

    def market_news(self, **filters: Any) -> dict[str, Any]:
        return self.market_intel_service.market_news(**filters)

    def refresh_market_intel(self, request: MarketRefreshRequest) -> dict[str, Any]:
        return self.market_intel_service.refresh_market_intel(request)

    def publish_strategy(self, request: StrategyRequest) -> dict[str, Any]:
        return self.signal_service.publish_strategy(request)

    def publish_discussion(self, request: DiscussionRequest) -> dict[str, Any]:
        return self.signal_service.publish_discussion(request)

    def signal_feed(self, **filters: Any) -> dict[str, Any]:
        return self.signal_service.signal_feed(**filters)

    def heartbeat(self) -> dict[str, Any]:
        return self.signal_service.heartbeat()


def _coalesce_env(value: str | None, env_name: str) -> str | None:
    candidate = value if value is not None else os.environ.get(env_name)
    if candidate is None:
        return None
    cleaned = str(candidate).strip()
    return cleaned or None


def _coalesce_int_env(
    value: int | None,
    env_name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    candidate: Any = value if value is not None else os.environ.get(env_name)
    try:
        parsed = int(candidate)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
