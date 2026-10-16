"""Read-only client for the public SSI iBoard market endpoints.

Two hosts are involved, and they do not share a unit: the board group endpoint
quotes prices in đồng while the chart endpoint quotes them in nghìn đồng.
Everything leaving this module is in đồng.

This path is display-only and deliberately outside the governed quant pipeline:
it writes no dataset revision, touches no domain table, and must never be wired
into an agent tool. See docs/architecture/overview.md.
"""

import asyncio
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

# Public, credential-free hosts, so they are constants rather than settings: a
# configurable URL would be an SSRF primitive with no token to protect by
# pinning it, and the repo already hardcodes its other open API (Tavily).
QUERY_BASE_URL = "https://iboard-query.ssi.com.vn"
STATS_BASE_URL = "https://iboard-api.ssi.com.vn/statistics"
USER_AGENT = "nexus-agent-market-display/0.1"

# The board group holding every HOSE stock -- 312 listings, all with a
# reference price. VNINDEX would also mix in covered warrants, ETFs, and fund
# certificates, and HOSE/HNX/UPCOM are not group names at all.
UNIVERSE_GROUP = "VNALL"
CHART_RESOLUTION = "1D"

# SSI quotes the board in đồng but the chart in nghìn đồng. Cross-checked
# against four symbols where chart close x 1000 equals that symbol's board
# refPrice: SSI 19.25 -> 19,250; ACB 20.6 -> 20,600; FPT 58 -> 58,000;
# AAA 7 -> 7,000.
CHART_PRICE_SCALE = 1000

SUPPORTED_RANGES: tuple[str, ...] = ("1mo", "3mo", "6mo", "1y")
_RANGE_DAYS = {"1mo": 31, "3mo": 92, "6mo": 183, "1y": 366}

_MAX_BARS = 400
# Vietnam has had no daylight saving since 1975, so a fixed offset is exact and
# avoids depending on the tzdata package being present in the container image.
_ICT = timezone(timedelta(hours=7))

_SYMBOL = re.compile(r"^[A-Z0-9]{3,10}$")
# SSI sends `tradingDate` as YYYYMMDD; the chart's `t` is a unix timestamp.
_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
# Provider error codes are echoed back to the browser, so only a code that looks
# like one is ever interpolated.
_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,32}$")

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
class BoardSnapshot:
    """The HOSE board, plus the session its reference prices belong to."""

    rows: tuple[dict[str, Any], ...]
    trading_date: str | None
    as_of: str
    stale: bool


@dataclass(frozen=True, slots=True)
class ChartSnapshot:
    """One symbol's daily bars and how old they are."""

    bars: tuple[dict[str, Any], ...]
    as_of: str
    stale: bool


@dataclass(frozen=True, slots=True)
class _CachedBoard:
    written_monotonic: float
    rows: tuple[dict[str, Any], ...]
    trading_date: str | None
    as_of: str


@dataclass(frozen=True, slots=True)
class _CachedChart:
    written_monotonic: float
    bars: tuple[dict[str, Any], ...]
    as_of: str


class MarketQuoteClient:
    """Fetches the HOSE board and daily bars for the market display page.

    Two caches, because the two endpoints change at different rates: the board
    is one request for all 312 symbols and its reference prices belong to a
    whole trading session, while a chart is per symbol and moves every session.

    The caches are per-instance, which means per-process. `langgraph dev` runs a
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
        universe_ttl_seconds: int = 900,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._universe_ttl_seconds = universe_ttl_seconds
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._board: _CachedBoard | None = None
        self._board_inflight: asyncio.Task[None] | None = None
        self._board_error: MarketQuoteError | None = None
        self._charts: dict[tuple[str, str], _CachedChart] = {}
        self._charts_inflight: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._chart_errors: dict[tuple[str, str], MarketQuoteError] = {}

    async def fetch_board(self) -> BoardSnapshot:
        """Every HOSE symbol with its reference, ceiling, and floor price.

        The board group already carries all three prices for all 312 symbols, so
        this costs no upstream call while the universe entry is fresh -- there is
        no per-symbol fan-out and no `POST /stock/multiple` batch to size.
        """
        cached = self._board
        if cached is not None and self._is_fresh(cached.written_monotonic, self._universe_ttl_seconds):
            return _board_snapshot(cached, stale=False)

        task = self._board_inflight
        if task is None:
            self._board_error = None
            task = asyncio.create_task(self._load_board())
            self._board_inflight = task
        # Shielded so one disconnecting browser cannot cancel the fetch that
        # every other waiter shares.
        await asyncio.shield(task)

        entry = self._board
        if entry is not None:
            return _board_snapshot(
                entry, stale=not self._is_fresh(entry.written_monotonic, self._universe_ttl_seconds)
            )
        if cached is not None:
            # Labeled-old data beats a blank board on a display-only page.
            return _board_snapshot(cached, stale=True)
        raise self._board_error or MarketQuoteError("upstream_error", _UNAVAILABLE)

    async def fetch(self, symbol: str, range_: str) -> ChartSnapshot:
        key = (symbol, range_)
        cached = self._charts.get(key)
        if cached is not None and self._is_fresh(cached.written_monotonic, self._cache_ttl_seconds):
            return _chart_snapshot(cached, stale=False)

        task = self._charts_inflight.get(key)
        if task is None:
            self._chart_errors.pop(key, None)
            task = asyncio.create_task(self._load_chart(key))
            self._charts_inflight[key] = task
        await asyncio.shield(task)

        entry = self._charts.get(key)
        if entry is not None:
            return _chart_snapshot(
                entry, stale=not self._is_fresh(entry.written_monotonic, self._cache_ttl_seconds)
            )
        if cached is not None:
            return _chart_snapshot(cached, stale=True)
        raise self._chart_errors.get(key) or MarketQuoteError("upstream_error", _UNAVAILABLE)

    async def _load_board(self) -> None:
        try:
            payload = await self._request(f"{QUERY_BASE_URL}/stock/group/{UNIVERSE_GROUP}")
            rows, trading_date = _parse_board(payload)
            self._board = _CachedBoard(
                written_monotonic=time.monotonic(),
                rows=rows,
                trading_date=trading_date,
                as_of=_now_ict(),
            )
        except MarketQuoteError as exc:
            # Only successful payloads are cached. A failure must neither evict
            # a good entry nor become one.
            self._board_error = exc
        finally:
            self._board_inflight = None

    async def _load_chart(self, key: tuple[str, str]) -> None:
        symbol, range_ = key
        try:
            payload = await self._request(
                f"{STATS_BASE_URL}/charts/history",
                params=_chart_params(symbol, range_),
            )
            bars = _parse_chart(symbol, payload)
            self._charts[key] = _CachedChart(
                written_monotonic=time.monotonic(),
                bars=bars,
                as_of=_now_ict(),
            )
        except MarketQuoteError as exc:
            self._chart_errors[key] = exc
        finally:
            self._charts_inflight.pop(key, None)

    def _is_fresh(self, written_monotonic: float, ttl_seconds: int) -> bool:
        return time.monotonic() - written_monotonic < ttl_seconds

    async def _request(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if self._http is None:
            # One pool for the whole process. The singleton that owns this
            # client lives as long as the app, so it is never closed.
            self._http = httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
        try:
            response = await self._http.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise MarketQuoteError("timeout", _TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise MarketQuoteError("upstream_error", _UNAVAILABLE) from exc

        if response.status_code in {429, 999}:
            # Not retried on purpose: the caches are the resilience mechanism,
            # and retrying a throttled provider multiplies the fan-out.
            raise MarketQuoteError("upstream_error", _RATE_LIMITED)
        if response.status_code >= 400:
            # SSI reports an unknown path or a missing parameter as a JSON
            # envelope with a 4xx status. There is no per-symbol 404 to
            # translate: an untraded symbol answers 200 with an empty series.
            raise MarketQuoteError("upstream_error", _UNAVAILABLE)
        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketQuoteError("bad_payload", _BAD_PAYLOAD) from exc

        code = payload.get("code") if isinstance(payload, dict) else None
        if code is not None and code != "SUCCESS":
            raise MarketQuoteError("upstream_error", _provider_code_message(code))
        return payload


def _provider_code_message(code: Any) -> str:
    if isinstance(code, str) and _ERROR_CODE.fullmatch(code) is not None:
        return f"The market data provider rejected the request ({code})."
    return _UNAVAILABLE


def _no_data_message(symbol: str) -> str:
    return f"No market data is available for {symbol}."


def _chart_params(symbol: str, range_: str) -> dict[str, Any]:
    """The window SSI wants, in unix seconds, for one symbol and range.

    A reversed window answers 200 with zero bars, so the two ends are always
    computed together and in order.
    """
    now = datetime.now(_ICT)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = _RANGE_DAYS.get(range_, _RANGE_DAYS["1mo"])
    return {
        "resolution": CHART_RESOLUTION,
        "symbol": symbol,
        "from": int((midnight - timedelta(days=days)).timestamp()),
        "to": int(now.timestamp()),
    }


def _parse_board(payload: Any) -> tuple[tuple[dict[str, Any], ...], str | None]:
    """The whole board, trimmed to the fields the page renders.

    The raw group payload is ~278 KB of per-listing metadata (ISIN, par value,
    halt flags, issuer, corporate events); only these eight fields reach the
    browser, and `error` has no place here because one request either yields the
    whole board or fails the whole route.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    trading_dates: list[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
        # A shape change should be loud: a malformed symbol or a duplicate means
        # the contract moved, not that the market shrank.
        symbol = _text(entry.get("stockSymbol"))
        if symbol is None or _SYMBOL.fullmatch(symbol) is None or symbol in seen:
            raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
        # Composition, by contrast, is skipped silently: the page says HOSE
        # stocks, so a warrant, ETF, or other exchange appearing in this group
        # is out of scope rather than an error.
        if not _is_hose_stock(entry):
            continue
        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": _text(entry.get("companyNameVi")) or _text(entry.get("companyNameEn")),
                "exchange": (_text(entry.get("exchange")) or "").upper() or None,
                # A listing can legitimately arrive without a published band.
                # Failing the whole board for it would blank all 312 rows to
                # hide one dash, so the price is nulled and the row kept.
                "ref_price": _number(entry.get("refPrice")),
                "ceiling": _number(entry.get("ceiling")),
                "floor": _number(entry.get("floor")),
                "foreign_room": _integer(entry.get("remainForeignQtty")),
                "trading_unit": _integer(entry.get("tradingUnit")),
            }
        )
        # Genuinely optional: a row without a session date still has prices, so
        # it is skipped rather than failing the board.
        trading_date = _compact_date(entry.get("tradingDate"))
        if trading_date is not None:
            trading_dates.append(trading_date)

    if not rows:
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
    return tuple(rows), max(trading_dates) if trading_dates else None


def _is_hose_stock(entry: dict[str, Any]) -> bool:
    """Whether a board entry is an ordinary HOSE share.

    Absent fields do not disqualify an entry: only a value that positively says
    otherwise does, so an unnamed exchange cannot silently empty the board.
    """
    exchange = _text(entry.get("exchange"))
    if exchange is not None and exchange.lower() != "hose":
        return False
    stock_type = _text(entry.get("stockType"))
    return stock_type is None or stock_type.lower() == "s"



def _parse_chart(symbol: str, payload: Any) -> tuple[dict[str, Any], ...]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)

    timestamps = data.get("t")
    closes = data.get("c")
    if not isinstance(timestamps, list) or not isinstance(closes, list):
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
    if len(timestamps) != len(closes):
        raise MarketQuoteError("bad_payload", _BAD_PAYLOAD)
    volumes = data.get("v")
    if not isinstance(volumes, list) or len(volumes) != len(timestamps):
        volumes = [None] * len(timestamps)

    bars: list[dict[str, Any]] = []
    for index, (stamp, close) in enumerate(zip(timestamps, closes)):
        value = _number(close)
        epoch = _number(stamp)
        if value is None or epoch is None:
            continue
        bars.append(
            {
                "time": _trading_date(int(epoch)),
                # The chart is quoted in nghìn đồng; the board is already in
                # đồng. Rounding keeps 20.6 x 1000 from reaching the browser as
                # 20600.000000000004.
                "close": float(round(value * CHART_PRICE_SCALE)),
                "volume": _integer(volumes[index]),
            }
        )
    if not bars:
        # SSI answers HTTP 200 with `code: SUCCESS` and empty arrays for a
        # symbol it does not trade, so an empty series -- not a 404 -- is the
        # no-data case here.
        raise MarketQuoteError("no_data", _no_data_message(symbol))
    return tuple(bars[-_MAX_BARS:])


def _board_snapshot(entry: _CachedBoard, *, stale: bool) -> BoardSnapshot:
    return BoardSnapshot(
        rows=entry.rows, trading_date=entry.trading_date, as_of=entry.as_of, stale=stale
    )


def _chart_snapshot(entry: _CachedChart, *, stale: bool) -> ChartSnapshot:
    return ChartSnapshot(bars=entry.bars, as_of=entry.as_of, stale=stale)


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


def _compact_date(value: Any) -> str | None:
    """SSI's `20261016` -> `2026-10-16`."""
    text = _text(value)
    matched = _COMPACT_DATE.fullmatch(text) if text is not None else None
    return f"{matched[1]}-{matched[2]}-{matched[3]}" if matched else None


def _trading_date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, _ICT).date().isoformat()


def _now_ict() -> str:
    return datetime.now(_ICT).isoformat(timespec="seconds")
