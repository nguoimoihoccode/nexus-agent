"""Market snapshot refresh and read use cases."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from worker.domain.market import (
    CATEGORY_DEFINITIONS,
    MARKET_CATEGORIES,
    WorkerConfigError,
    activity_level,
    build_news_summary,
    category_public_fields,
    datetime_to_iso_z,
    filter_news_section,
    normalize_news_item,
    normalize_symbols_filter,
    parse_iso_datetime,
    utc_now_iso,
    with_symbol_filter_metadata,
)
from worker.models import MarketRefreshRequest
from worker.ports import MarketNewsProvider, SignalRepository


class MarketIntelService:
    def __init__(
        self,
        repository: SignalRepository,
        provider: MarketNewsProvider,
        *,
        api_key: str | None,
        lookback_hours: int,
        news_limit: int,
        refresh_interval_seconds: int,
        stale_after_seconds: int,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.api_key = api_key
        self.lookback_hours = lookback_hours
        self.news_limit = news_limit
        self.refresh_interval_seconds = refresh_interval_seconds
        self.stale_after_seconds = stale_after_seconds

    def market_overview(self) -> dict[str, Any]:
        snapshot = self.repository.load_snapshot("market_overview", "default")
        if isinstance(snapshot, dict):
            return self._with_market_freshness(snapshot)
        return self._with_market_freshness(
            {
                "available": False,
                "last_updated_at": None,
                "macro_verdict": None,
                "macro_bullish_count": 0,
                "macro_total_count": 0,
                "macro_summary": None,
                "macro_summary_zh": None,
                "etf_direction": None,
                "etf_summary": None,
                "etf_summary_zh": None,
                "etf_tracked_count": 0,
                "featured_stock_count": 0,
                "news_status": "quiet",
                "headline_count": 0,
                "active_categories": 0,
                "top_source": None,
                "latest_headline": None,
                "latest_item_time": None,
                "categories": [],
            }
        )

    def market_news(
        self,
        *,
        category: str | None = None,
        limit: int = 5,
        symbols: str | Iterable[str] | None = None,
    ) -> dict[str, Any]:
        normalized_category = (category or "").strip().lower() or "all"
        symbol_filter = normalize_symbols_filter(symbols)
        snapshot = self.repository.load_snapshot("market_news", normalized_category)
        if isinstance(snapshot, dict):
            return self._with_market_freshness(
                self._limit_news_payload(snapshot, limit, symbols=symbol_filter)
            )
        if normalized_category == "all":
            categories = [self._empty_news_section(item) for item in MARKET_CATEGORIES]
        else:
            definition = CATEGORY_DEFINITIONS.get(normalized_category)
            categories = [self._empty_news_section(definition)] if definition else []
        payload = {
            "categories": categories,
            "last_updated_at": None,
            "total_items": 0,
            "available": False,
        }
        return self._with_market_freshness(
            with_symbol_filter_metadata(payload, symbol_filter)
        )

    def refresh_market_intel(self, request: MarketRefreshRequest) -> dict[str, Any]:
        api_key = (self.api_key or "").strip()
        if not api_key or api_key == "demo":
            raise WorkerConfigError("ALPHA_VANTAGE_API_KEY is not configured.")
        refreshed_at = utc_now_iso()
        categories = request.categories or [
            str(item["category"]) for item in MARKET_CATEGORIES
        ]
        lookback_hours = max(
            1,
            min(int(request.lookback_hours or self.lookback_hours), 720),
        )
        limit = max(1, min(int(request.limit or self.news_limit), 100))
        results: list[dict[str, Any]] = []
        successful_categories: list[str] = []
        for category in categories:
            definition = CATEGORY_DEFINITIONS[category]
            try:
                payload = self._refresh_market_news_category(
                    category,
                    definition,
                    api_key=api_key,
                    lookback_hours=lookback_hours,
                    limit=limit,
                    refreshed_at=refreshed_at,
                )
            except Exception as exc:
                results.append(
                    {
                        "category": category,
                        "available": False,
                        "item_count": 0,
                        "error": str(exc)[:300],
                    }
                )
                continue
            successful_categories.append(category)
            results.append(
                {
                    "category": category,
                    "available": bool(payload.get("available")),
                    "item_count": int((payload.get("summary") or {}).get("item_count") or 0),
                    "error": None,
                }
            )
        if successful_categories:
            all_news = self._build_all_market_news_payload()
            overview = self._build_market_overview_payload(all_news)
            self.repository.upsert_snapshot("market_news", "all", all_news, refreshed_at)
            self.repository.upsert_snapshot(
                "market_overview",
                "default",
                overview,
                refreshed_at,
            )
        else:
            overview = self.market_overview()
        return {
            "success": True,
            "refreshed_at": refreshed_at,
            "categories": results,
            "overview": self._with_market_freshness(overview),
        }

    def _refresh_market_news_category(
        self,
        category: str,
        definition: dict[str, str | dict[str, str]],
        *,
        api_key: str,
        lookback_hours: int,
        limit: int,
        refreshed_at: str,
    ) -> dict[str, Any]:
        params = self.provider.parameters(
            definition,
            api_key=api_key,
            lookback_hours=lookback_hours,
            limit=limit,
        )
        items = self._normalize_market_news_payload(self.provider.fetch(category, params))
        payload = {
            **category_public_fields(definition),
            "items": items,
            "summary": build_news_summary(category, items),
            "created_at": refreshed_at,
            "available": bool(items),
        }
        self.repository.upsert_snapshot("market_news", category, payload, refreshed_at)
        return payload

    @staticmethod
    def _normalize_market_news_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if "Note" in payload:
            raise RuntimeError(str(payload.get("Note") or "Alpha Vantage rate limit"))
        if "Information" in payload:
            raise RuntimeError(
                str(payload.get("Information") or "Alpha Vantage information")
            )
        feed = payload.get("feed")
        if not isinstance(feed, list):
            raise RuntimeError("Alpha Vantage payload did not include a feed list")
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_item in feed:
            if not isinstance(raw_item, dict):
                continue
            item = normalize_news_item(raw_item)
            if item is None:
                continue
            dedupe_key = item["url"] or f'{item["title"]}::{item["source"]}'
            if dedupe_key not in seen:
                seen.add(dedupe_key)
                items.append(item)
        return sorted(
            items,
            key=lambda current: current.get("time_published") or "",
            reverse=True,
        )

    def _build_all_market_news_payload(self) -> dict[str, Any]:
        sections: list[dict[str, Any]] = []
        for definition in MARKET_CATEGORIES:
            category = str(definition["category"])
            snapshot = self.repository.load_snapshot("market_news", category)
            sections.append(
                snapshot
                if isinstance(snapshot, dict)
                else self._empty_news_section(definition)
            )
        total_items = sum(
            int((section.get("summary") or {}).get("item_count") or 0)
            for section in sections
        )
        last_updated_at = max(
            (section.get("created_at") for section in sections if section.get("created_at")),
            default=None,
        )
        return {
            "categories": sections,
            "last_updated_at": last_updated_at,
            "total_items": total_items,
            "available": any(section.get("available") for section in sections),
        }

    @staticmethod
    def _build_market_overview_payload(news_payload: dict[str, Any]) -> dict[str, Any]:
        categories = [
            section
            for section in news_payload.get("categories") or []
            if isinstance(section, dict)
        ]
        total_items = int(news_payload.get("total_items") or 0)
        available_categories = [section for section in categories if section.get("available")]
        source_counter: Counter[str] = Counter()
        latest_headline = None
        latest_item_time = None
        for section in categories:
            for item in section.get("items") or []:
                if not isinstance(item, dict):
                    continue
                item_source = str(item.get("source") or "").strip()
                if item_source:
                    source_counter[item_source] += 1
                item_time = item.get("time_published")
                if item_time and (
                    latest_item_time is None or str(item_time) > str(latest_item_time)
                ):
                    latest_item_time = str(item_time)
                    latest_headline = item.get("title")
        return {
            "available": bool(available_categories),
            "last_updated_at": news_payload.get("last_updated_at"),
            "macro_verdict": None,
            "macro_bullish_count": 0,
            "macro_total_count": 0,
            "macro_summary": None,
            "macro_summary_zh": None,
            "etf_direction": None,
            "etf_summary": None,
            "etf_summary_zh": None,
            "etf_tracked_count": 0,
            "featured_stock_count": 0,
            "news_status": activity_level(total_items),
            "headline_count": total_items,
            "active_categories": len(available_categories),
            "top_source": source_counter.most_common(1)[0][0] if source_counter else None,
            "latest_headline": latest_headline,
            "latest_item_time": latest_item_time,
            "categories": [
                {
                    "category": section.get("category"),
                    "label": section.get("label"),
                    "label_zh": section.get("label_zh"),
                    "activity_level": (section.get("summary") or {}).get(
                        "activity_level",
                        "quiet",
                    ),
                    "item_count": (section.get("summary") or {}).get("item_count", 0),
                    "top_headline": (section.get("summary") or {}).get("top_headline"),
                    "top_source": (section.get("summary") or {}).get("top_source"),
                    "created_at": section.get("created_at"),
                }
                for section in categories
            ],
        }

    @staticmethod
    def _limit_news_payload(
        payload: dict[str, Any],
        limit: int,
        *,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 12))
        if "categories" not in payload and "items" in payload:
            section = filter_news_section(payload, symbols)
            section["items"] = list(section.get("items") or [])[:safe_limit]
            item_count = int((section.get("summary") or {}).get("item_count") or 0)
            return with_symbol_filter_metadata(
                {
                    "categories": [section],
                    "last_updated_at": payload.get("created_at"),
                    "total_items": item_count,
                    "available": bool(section.get("available")),
                },
                symbols,
            )
        limited = dict(payload)
        categories = []
        for section in payload.get("categories") or []:
            if not isinstance(section, dict):
                continue
            copied = filter_news_section(section, symbols)
            copied["items"] = list(copied.get("items") or [])[:safe_limit]
            categories.append(copied)
        total_items = sum(
            int((section.get("summary") or {}).get("item_count") or 0)
            for section in categories
        )
        limited.update(
            {
                "categories": categories,
                "total_items": (
                    total_items if symbols else payload.get("total_items", total_items)
                ),
                "available": any(section.get("available") for section in categories),
            }
        )
        return with_symbol_filter_metadata(limited, symbols)

    def _with_market_freshness(self, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        last_updated_at = enriched.get("last_updated_at")
        enriched.update(
            self._market_freshness_metadata(
                str(last_updated_at) if last_updated_at else None
            )
        )
        return enriched

    def _market_freshness_metadata(self, last_updated_at: str | None) -> dict[str, Any]:
        base = {
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "stale_after_seconds": self.stale_after_seconds,
        }
        if not last_updated_at or (parsed := parse_iso_datetime(last_updated_at)) is None:
            return {**base, "stale": True, "next_refresh_at": None}
        next_refresh = parsed + timedelta(seconds=self.refresh_interval_seconds)
        stale_after = parsed + timedelta(seconds=self.stale_after_seconds)
        return {
            **base,
            "stale": datetime.now(timezone.utc) >= stale_after,
            "next_refresh_at": datetime_to_iso_z(next_refresh),
        }

    @staticmethod
    def _empty_news_section(
        definition: dict[str, str | dict[str, str]],
    ) -> dict[str, Any]:
        return {
            **category_public_fields(definition),
            "items": [],
            "summary": {
                "category": definition["category"],
                "item_count": 0,
                "activity_level": "unavailable",
            },
            "created_at": None,
            "available": False,
        }
