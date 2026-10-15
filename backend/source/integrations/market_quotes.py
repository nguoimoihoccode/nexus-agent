"""Read-only client for the public Yahoo Finance chart endpoint.

This path is display-only and deliberately outside the governed quant pipeline:
it writes no dataset revision, touches no domain table, and must never be wired
into an agent tool. See docs/architecture/overview.md.
"""

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from urllib.parse import quote

import httpx

# A public, credential-free host, so it is a constant rather than a setting:
# a configurable URL would be an SSRF primitive with no token to protect by
# pinning it, and the repo already hardcodes its other open API (Tavily).
BASE_URL = "https://query1.finance.yahoo.com"
USER_AGENT = "nexus-agent-market-display/0.1"

SUPPORTED_RANGES: tuple[str, ...] = ("1mo", "3mo", "6mo", "1y")
# The quote list and the default chart view request the same range, so opening
# a chart for a symbol the list already loaded costs no upstream call.
QUOTE_RANGE = "1mo"

_INTERVAL = "1d"
_MAX_BARS = 400
_MAX_CONCURRENCY = 6
# Vietnam has had no daylight saving since 1975, so a fixed offset is exact and
# avoids depending on the tzdata package being present in the container image.
_ICT = timezone(timedelta(hours=7))

_BAD_PAYLOAD = "The market data provider returned an invalid response."
_UNAVAILABLE = "The market data provider is unavailable."
_RATE_LIMITED = "The market data provider is rate limiting this deployment."
_TIMEOUT = "The market data provider did not respond in time."


class MarketQuoteError(Exception):
    """A provider failure carrying a stable code the route maps to a status."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ChartSnapshot:
    """One symbol's normalized quote and bars, plus how old they are."""

    quote: dict[str, Any]
    bars: tuple[dict[str, Any], ...]
    as_of: str
    stale: bool


@dataclass(frozen=True, slots=True)
class _Cached:
    written_monotonic: float
    quote: dict[str, Any]
    bars: tuple[dict[str, Any], ...]
    as_of: str


def yahoo_symbol(symbol: str) -> str:
    """Provider symbol for a HOSE ticker. HNX and UPCOM have no listing."""
    return f"{symbol.strip().upper()}.VN"


class MarketQuoteClient:
    """Fetches daily bars and live quotes for the market display page.

    The cache is per-instance, which means per-process. `langgraph dev` runs a
    single event loop, so every mutation below happens on the loop thread and no
    lock is needed -- but that is only true because the miss path does its
    bookkeeping without an intervening await, and because this client is never
    called through `asyncio.to_thread`. Adding a blocking call here breaks both.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: int = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._cache: dict[tuple[str, str], _Cached] = {}
        self._inflight: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._errors: dict[tuple[str, str], MarketQuoteError] = {}

    async def fetch(self, symbol: str, range_: str) -> ChartSnapshot:
        key = (symbol, range_)
        cached = self._cache.get(key)
        if cached is not None and self._is_fresh(cached):
            return _snapshot(cached, stale=False)

        task = self._inflight.get(key)
        if task is None:
            self._errors.pop(key, None)
            task = asyncio.create_task(self._load(key))
            self._inflight[key] = task
        # Shielded so one disconnecting browser cannot cancel the fetch that
        # every other waiter shares.
        await asyncio.shield(task)

        entry = self._cache.get(key)
        if entry is not None:
            return _snapshot(entry, stale=not self._is_fresh(entry))
        if cached is not None:
            # Labeled-old data beats a blank row on a display-only page.
            return _snapshot(cached, stale=True)
        raise self._errors.get(key) or MarketQuoteError("upstream_error", _UNAVAILABLE)

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> tuple[list[dict[str, Any]], str, bool]:
        """Quote rows in input order, with the oldest fetch time and staleness.

        A symbol that fails becomes a row carrying its error code rather than
        failing the batch, so one delisted ticker cannot blank the page.
        """
        semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
        first_error: MarketQuoteError | None = None

        async def row(symbol: str) -> tuple[dict[str, Any], ChartSnapshot | None]:
            nonlocal first_error
            async with semaphore:
                try:
                    snapshot = await self.fetch(symbol, QUOTE_RANGE)
                except MarketQuoteError as exc:
                    if first_error is None:
                        first_error = exc
                    return _unavailable_row(symbol, exc.code), None
            return snapshot.quote, snapshot

        results = await asyncio.gather(*(row(symbol) for symbol in symbols))
        snapshots = [snapshot for _, snapshot in results if snapshot is not None]
        if not snapshots:
            # Every symbol failed. Surface the outage instead of rendering a
            # grid of identical grey rows that looks like a working page.
            raise first_error or MarketQuoteError("upstream_error", _UNAVAILABLE)
        return (
            [quote_row for quote_row, _ in results],
            min(snapshot.as_of for snapshot in snapshots),
            any(snapshot.stale for snapshot in snapshots),
        )

    async def _load(self, key: tuple[str, str]) -> None:
        symbol, range_ = key
        try:
            payload = await self._request(symbol, range_)
            quote_row, bars = _parse_chart(symbol, payload)
            self._cache[key] = _Cached(
                written_monotonic=time.monotonic(),
                quote=quote_row,
                bars=bars,
                as_of=_now_ict(),
            )
        except MarketQuoteError as exc:
            # Only successful payloads are cached. A failure must neither evict
            # a good entry nor become one.
            self._errors[key] = exc
        finally:
            self._inflight.pop(key, None)

    def _is_fresh(self, entry: _Cached) -> bool:
        return time.monotonic() - entry.written_monotonic < self._cache_ttl_seconds

    async def _request(self, symbol: str, range_: str) -> Any:
        url = f"{BASE_URL}/v8/finance/chart/{quote(yahoo_symbol(symbol), safe='')}"
        if self._http is None:
            # One pool for the whole process. The singleton that owns this
            # client lives as long as the app, so it is never closed.
            self._http = httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
        try:
            response = await self._http.get(
                url, params={"range": range_, "interval": _INTERVAL}
            )
        except httpx.TimeoutException as exc:
            raise MarketQuoteError("timeout", _TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise MarketQuoteError("upstream_error", _UNAVAILABLE) from exc

        if response.status_code == 404:
            raise MarketQuoteError("no_data", _no_data_message(symbol))
        if response.status_code in {429, 999}:
            # Not retried on purpose: the cache is the resilience mechanism, and
            # retrying a throttled provider multiplies the fan-out.
            raise MarketQuoteError("upstream_error", _RATE_LIMITED)
        if response.status_code >= 400:
            raise MarketQuoteError("upstream_error", _UNAVAILABLE)
        try:
            return response.json()
        except ValueError as exc:
            raise MarketQuoteError("bad_payload", _BAD_PAYLOAD) from exc


def _no_data_message(symbol: str) -> str:
    return f"No market data is available for {symbol}."


def _parse_chart(
    symbol: str, payload: Any
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)

    # Yahoo answers HTTP 200 with a populated `error` and an empty result for an
    # unknown symbol, so this branch is not reachable by status code alone.
    if chart.get("error"):
        raise MarketQuoteError("no_data", _no_data_message(symbol))

    results = chart.get("result")
    if not isinstance(results, list) or not results:
        raise MarketQuoteError("no_data", _no_data_message(symbol))
    result = results[0]
    if not isinstance(result, dict):
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
    meta = result.get("meta")
    if not isinstance(meta, dict):
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)

    # `regularMarketChangePercent` is already scaled: FPT reports 1.576 for a
    # +1.576% move. Verified against four symbols; do not multiply by 100.
    #
    # `previousClose` is absent from this endpoint, and `chartPreviousClose` is
    # the close before the *requested range* -- a month ago for QUOTE_RANGE.
    # Exposing that as yesterday's close would have printed FPT's month move
    # (-2.8%) as its daily move (+1.576%), so no previous close is exposed.
    quote_row: dict[str, Any] = {
        "symbol": symbol,
        "name": _text(meta.get("shortName")) or _text(meta.get("longName")),
        "exchange": _text(meta.get("fullExchangeName"))
        or _text(meta.get("exchangeName")),
        "currency": _text(meta.get("currency")),
        "price": _number(meta.get("regularMarketPrice")),
        "change_percent": _number(meta.get("regularMarketChangePercent")),
        "volume": _integer(meta.get("regularMarketVolume")),
        "fifty_two_week_high": _number(meta.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low": _number(meta.get("fiftyTwoWeekLow")),
        "market_time": _market_time(meta.get("regularMarketTime")),
        "error": None,
    }
    if quote_row["price"] is None:
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
    return quote_row, _bars(result)


def _bars(result: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    indicators = result.get("indicators")
    blocks = indicators.get("quote") if isinstance(indicators, dict) else None
    if not isinstance(blocks, list) or not blocks or not isinstance(blocks[0], dict):
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
    timestamps = result.get("timestamp")
    closes = blocks[0].get("close")
    if not isinstance(timestamps, list) or not isinstance(closes, list):
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
    if len(timestamps) != len(closes):
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
    volumes = blocks[0].get("volume")
    if not isinstance(volumes, list) or len(volumes) != len(timestamps):
        volumes = [None] * len(timestamps)

    bars: list[dict[str, Any]] = []
    for index, (stamp, close) in enumerate(zip(timestamps, closes)):
        # The newest bar is null while its session is still open, so skipping
        # null closes is the normal case here, not an edge case.
        value = _number(close)
        epoch = _number(stamp)
        if value is None or epoch is None:
            continue
        bars.append(
            {
                "time": _trading_date(int(epoch)),
                "close": value,
                "volume": _integer(volumes[index]),
            }
        )
    return tuple(bars[-_MAX_BARS:])


def _unavailable_row(symbol: str, code: str) -> dict[str, Any]:
    """A row of the same shape as a successful one, with only `error` set.

    Keeping one shape means the client contract has a single schema instead of a
    discriminated union the table would have to switch on.
    """
    return {
        "symbol": symbol,
        "name": None,
        "exchange": None,
        "currency": None,
        "price": None,
        "change_percent": None,
        "volume": None,
        "fifty_two_week_high": None,
        "fifty_two_week_low": None,
        "market_time": None,
        "error": code,
    }


def _snapshot(entry: _Cached, *, stale: bool) -> ChartSnapshot:
    return ChartSnapshot(
        quote=entry.quote, bars=entry.bars, as_of=entry.as_of, stale=stale
    )


def _number(value: Any) -> float | None:
    # bool is an int subclass, and NaN/inf would serialize as invalid JSON.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _trading_date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, _ICT).date().isoformat()


def _market_time(value: Any) -> str | None:
    epoch = _number(value)
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, _ICT).isoformat(timespec="seconds")


def _now_ict() -> str:
    return datetime.now(_ICT).isoformat(timespec="seconds")
