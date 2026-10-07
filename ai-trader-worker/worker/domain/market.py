"""Framework-independent market-news normalization and signal vocabulary."""

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

MARKET_CATEGORIES: tuple[dict[str, str | dict[str, str]], ...] = (
    {
        "category": "equities",
        "label": "Equities",
        "label_zh": "Equities",
        "description": "Stocks, ETFs, and company market developments.",
        "description_zh": "Stocks, ETFs, and company market developments.",
        "alpha_vantage": {"topics": "financial_markets"},
    },
    {
        "category": "macro",
        "label": "Macro",
        "label_zh": "Macro",
        "description": "Macro regime, policy, and broad economic context.",
        "description_zh": "Macro regime, policy, and broad economic context.",
        "alpha_vantage": {"topics": "economy_macro"},
    },
    {
        "category": "crypto",
        "label": "Crypto",
        "label_zh": "Crypto",
        "description": "Crypto market headlines anchored on BTC and ETH.",
        "description_zh": "Crypto market headlines anchored on BTC and ETH.",
        "alpha_vantage": {"tickers": "CRYPTO:BTC,CRYPTO:ETH"},
    },
    {
        "category": "commodities",
        "label": "Commodities",
        "label_zh": "Commodities",
        "description": "Energy, transport, and commodity-linked events.",
        "description_zh": "Energy, transport, and commodity-linked events.",
        "alpha_vantage": {"topics": "energy_transportation"},
    },
)
CATEGORY_DEFINITIONS = {
    str(definition["category"]): definition for definition in MARKET_CATEGORIES
}


class WorkerConfigError(RuntimeError):
    """The worker is missing required operator configuration."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def category_public_fields(
    definition: dict[str, str | dict[str, str]],
) -> dict[str, str]:
    return {
        "category": str(definition["category"]),
        "label": str(definition["label"]),
        "label_zh": str(definition["label_zh"]),
        "description": str(definition["description"]),
        "description_zh": str(definition["description_zh"]),
    }


def normalize_news_item(item: dict[str, Any]) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    ticker_sentiment = []
    for entry in item.get("ticker_sentiment") or []:
        if not isinstance(entry, dict):
            continue
        ticker = str(entry.get("ticker") or "").strip()
        if ticker:
            ticker_sentiment.append(
                {
                    "ticker": ticker,
                    "relevance_score": to_float(entry.get("relevance_score")),
                    "sentiment_score": to_float(entry.get("ticker_sentiment_score")),
                    "sentiment_label": entry.get("ticker_sentiment_label"),
                }
            )
    topics = []
    for entry in item.get("topics") or []:
        if not isinstance(entry, dict):
            continue
        topic = str(entry.get("topic") or "").strip()
        if topic:
            topics.append(
                {
                    "topic": topic,
                    "relevance_score": to_float(entry.get("relevance_score")),
                }
            )
    return {
        "title": title,
        "url": str(item.get("url") or "").strip(),
        "source": str(item.get("source") or "").strip(),
        "summary": str(item.get("summary") or "").strip(),
        "banner_image": item.get("banner_image"),
        "time_published": parse_alpha_vantage_time(item.get("time_published")),
        "overall_sentiment_score": to_float(item.get("overall_sentiment_score")),
        "overall_sentiment_label": item.get("overall_sentiment_label"),
        "ticker_sentiment": ticker_sentiment,
        "topics": topics,
    }


def build_news_summary(category: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    source_counter = Counter(
        str(item.get("source"))
        for item in items
        if str(item.get("source") or "").strip()
    )
    sentiment_counter = Counter(
        str(item.get("overall_sentiment_label") or "neutral").lower()
        for item in items
    )
    symbol_counter: Counter[str] = Counter()
    for item in items:
        for entry in item.get("ticker_sentiment") or []:
            if isinstance(entry, dict) and (ticker := str(entry.get("ticker") or "").strip()):
                symbol_counter[ticker] += 1
    return {
        "category": category,
        "item_count": len(items),
        "activity_level": activity_level(len(items)),
        "top_headline": items[0].get("title") if items else None,
        "latest_item_time": items[0].get("time_published") if items else None,
        "top_source": source_counter.most_common(1)[0][0] if source_counter else None,
        "top_symbols": [symbol for symbol, _ in symbol_counter.most_common(8)],
        "sentiment_breakdown": dict(sentiment_counter),
    }


def filter_news_section(
    section: dict[str, Any],
    symbols: list[str] | None,
) -> dict[str, Any]:
    copied = dict(section)
    items = [item for item in copied.get("items") or [] if isinstance(item, dict)]
    if symbols:
        items = [item for item in items if news_item_matches_symbols(item, symbols)]
        category = str(copied.get("category") or "")
        copied["summary"] = build_news_summary(category, items)
        copied["available"] = bool(items)
    copied["items"] = items
    return copied


def news_item_matches_symbols(item: dict[str, Any], symbols: list[str]) -> bool:
    wanted = set(symbols)
    return any(
        isinstance(entry, dict) and normalize_symbol(entry.get("ticker")) in wanted
        for entry in item.get("ticker_sentiment") or []
    )


def with_symbol_filter_metadata(
    payload: dict[str, Any],
    symbols: list[str] | None,
) -> dict[str, Any]:
    if not symbols:
        return payload
    matched: set[str] = set()
    for section in payload.get("categories") or []:
        if not isinstance(section, dict):
            continue
        for item in section.get("items") or []:
            if not isinstance(item, dict):
                continue
            for entry in item.get("ticker_sentiment") or []:
                if isinstance(entry, dict):
                    ticker = normalize_symbol(entry.get("ticker"))
                    if ticker in symbols:
                        matched.add(ticker)
    return {
        **payload,
        "filtered": True,
        "symbols_filter": symbols,
        "matched_symbols": [symbol for symbol in symbols if symbol in matched],
        "unmatched_symbols": [symbol for symbol in symbols if symbol not in matched],
    }


def normalize_symbols_filter(
    symbols: str | Iterable[str] | None,
) -> list[str] | None:
    if symbols is None:
        return None
    raw_values = symbols.split(",") if isinstance(symbols, str) else symbols
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        symbol = normalize_symbol(value)
        if symbol and symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
    return normalized or None


def normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def activity_level(item_count: int) -> str:
    if item_count >= 20:
        return "elevated"
    if item_count >= 8:
        return "active"
    if item_count > 0:
        return "calm"
    return "quiet"


def to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_alpha_vantage_time(value: Any) -> str | None:
    if not value or not (text := str(value).strip()):
        return None
    try:
        parsed = datetime.strptime(text, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    return datetime_to_iso_z(parsed)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value or not (cleaned := value.strip()):
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def datetime_to_iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def normalize_values(values: str | Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    raw_values = values.split(",") if isinstance(values, str) else list(values)
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


def dump_json(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=True)


def load_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item).strip()]


def utc_now_iso() -> str:
    return datetime_to_iso_z(datetime.now(timezone.utc))


def timestamp_from_iso(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int(parsed.timestamp())
