"""Focused tests for the read-only market display path."""

import asyncio
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import HTTPException, Response

import source.integrations.market_quotes as market_quotes_module
from source.core.market_universe import HOSE_LARGE_CAP_SYMBOLS
from source.http_app import app, market_history, market_quotes
from source.integrations.market_quotes import (
    MarketQuoteClient,
    MarketQuoteError,
    yahoo_symbol,
)

ACTOR = "v1-" + "a" * 64


def chart_payload(
    *,
    price=57_200.0,
    change_percent=-1.209,
    closes=(59_700.0, 57_900.0),
    timestamps=(1_791_770_400, 1_791_856_800),
    volumes=(1_000, 2_000),
    meta_override=None,
):
    meta = {
        "symbol": "VNM.VN",
        "currency": "VND",
        "exchangeName": "VSE",
        "fullExchangeName": "HOSE",
        "shortName": "VIETNAM DAIRY PRODUCTS JSC",
        "longName": "Vietnam Dairy Products Joint Stock Company",
        "regularMarketPrice": price,
        "regularMarketChangePercent": change_percent,
        "regularMarketVolume": 1_988_733,
        "regularMarketTime": 1_792_050_318,
        "fiftyTwoWeekHigh": 75_500.0,
        "fiftyTwoWeekLow": 54_600.0,
    }
    meta.update(meta_override or {})
    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": list(timestamps),
                    "indicators": {
                        "quote": [{"close": list(closes), "volume": list(volumes)}]
                    },
                }
            ],
            "error": None,
        }
    }


def client_for(handler, **kwargs):
    return MarketQuoteClient(
        timeout_seconds=10.0,
        cache_ttl_seconds=kwargs.pop("cache_ttl_seconds", 60),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def ok_handler(seen, payload=None):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload if payload is not None else chart_payload())

    return handler


class MarketQuoteClientTests(unittest.TestCase):
    def test_quote_row_is_normalized_from_the_provider_payload(self):
        client = client_for(ok_handler([]))
        snapshot = asyncio.run(client.fetch("VNM", "1mo"))

        row = snapshot.quote
        self.assertEqual(row["symbol"], "VNM")
        self.assertEqual(row["name"], "VIETNAM DAIRY PRODUCTS JSC")
        self.assertEqual(row["exchange"], "HOSE")
        self.assertEqual(row["currency"], "VND")
        self.assertEqual(row["price"], 57_200.0)
        self.assertEqual(row["change_percent"], -1.209)
        self.assertEqual(row["volume"], 1_988_733)
        self.assertEqual(row["fifty_two_week_high"], 75_500.0)
        self.assertEqual(row["fifty_two_week_low"], 54_600.0)
        self.assertIsNone(row["error"])
        # Vietnam is UTC+7 year round, so a fixed offset is exact.
        self.assertTrue(row["market_time"].endswith("+07:00"), row["market_time"])
        self.assertFalse(snapshot.stale)
        self.assertTrue(snapshot.as_of.endswith("+07:00"), snapshot.as_of)

    def test_bars_carry_the_ict_trading_date(self):
        client = client_for(ok_handler([]))
        snapshot = asyncio.run(client.fetch("VNM", "1mo"))

        self.assertEqual(
            [bar["time"] for bar in snapshot.bars], ["2026-10-12", "2026-10-13"]
        )
        self.assertEqual(snapshot.bars[1]["close"], 57_900.0)
        self.assertEqual(snapshot.bars[1]["volume"], 2_000)

    def test_symbol_uses_the_hose_suffix_and_a_daily_interval(self):
        seen = []
        client = client_for(ok_handler(seen))
        asyncio.run(client.fetch("FPT", "3mo"))

        self.assertEqual(yahoo_symbol("fpt"), "FPT.VN")
        self.assertEqual(seen[0].url.path, "/v8/finance/chart/FPT.VN")
        self.assertEqual(seen[0].url.params["range"], "3mo")
        self.assertEqual(seen[0].url.params["interval"], "1d")

    def test_no_internal_identity_headers_are_sent(self):
        # The provider is a third party: an actor pseudonym or thread ID would be
        # an identifier leak. This is why the client does not subclass
        # WorkerHttpClient, which injects both.
        seen = []
        client = client_for(ok_handler(seen))
        asyncio.run(client.fetch("VNM", "1mo"))

        headers = seen[0].headers
        self.assertNotIn("authorization", headers)
        self.assertNotIn("x-nexus-actor-key", headers)
        self.assertNotIn("x-nexus-thread-id", headers)
        self.assertTrue(headers["user-agent"])

    def test_null_closes_are_dropped_because_the_open_session_bar_is_null(self):
        payload = chart_payload(
            closes=(58_400.0, 57_900.0, None),
            timestamps=(1_791_770_400, 1_791_856_800, 1_792_029_600),
            volumes=(10, 20, None),
        )
        client = client_for(ok_handler([], payload))
        snapshot = asyncio.run(client.fetch("VNM", "1mo"))

        self.assertEqual(len(snapshot.bars), 2)
        self.assertEqual(snapshot.bars[-1]["close"], 57_900.0)

    def test_mismatched_timestamp_and_close_arrays_are_a_bad_payload(self):
        payload = chart_payload(closes=(57_900.0,), timestamps=(1_791_770_400, 1_791_856_800))
        client = client_for(ok_handler([], payload))

        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client.fetch("VNM", "1mo"))
        self.assertEqual(raised.exception.code, "bad_payload")

    def test_http_404_maps_to_no_data(self):
        def handler(request):
            return httpx.Response(404, json={"chart": {"result": None, "error": None}})

        client = client_for(handler)
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client.fetch("ZZZ", "1mo"))
        self.assertEqual(raised.exception.code, "no_data")

    def test_200_with_a_chart_error_maps_to_no_data(self):
        # Yahoo answers 200 with a populated error for an unknown symbol, so the
        # status code alone is not enough to detect it.
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "chart": {
                        "result": None,
                        "error": {"code": "Not Found", "description": "No data found"},
                    }
                },
            )

        client = client_for(handler)
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client.fetch("ZZZ", "1mo"))
        self.assertEqual(raised.exception.code, "no_data")

    def test_timeout_and_transport_failures_map_to_their_codes(self):
        def timeout_handler(request):
            raise httpx.ConnectTimeout("too slow")

        def connect_handler(request):
            raise httpx.ConnectError("refused")

        with self.assertRaises(Exception) as timed_out:
            asyncio.run(client_for(timeout_handler).fetch("VNM", "1mo"))
        self.assertEqual(timed_out.exception.code, "timeout")

        with self.assertRaises(Exception) as unreachable:
            asyncio.run(client_for(connect_handler).fetch("VNM", "1mo"))
        self.assertEqual(unreachable.exception.code, "upstream_error")

    def test_non_json_body_is_a_bad_payload(self):
        def handler(request):
            return httpx.Response(200, content=b"<html>nope</html>")

        client = client_for(handler)
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client.fetch("VNM", "1mo"))
        self.assertEqual(raised.exception.code, "bad_payload")

    def test_rate_limiting_is_mapped_and_not_retried(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(429, json={"error": "slow down"})

        client = client_for(handler)
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client.fetch("VNM", "1mo"))
        self.assertEqual(raised.exception.code, "upstream_error")
        self.assertEqual(len(seen), 1)

    def test_cache_hit_does_not_refetch(self):
        seen = []
        client = client_for(ok_handler(seen))

        first = asyncio.run(client.fetch("VNM", "1mo"))
        second = asyncio.run(client.fetch("VNM", "1mo"))

        self.assertEqual(len(seen), 1)
        self.assertEqual(first.quote, second.quote)
        self.assertEqual(first.bars, second.bars)

    def test_the_quote_list_and_the_default_chart_share_one_fetch(self):
        seen = []
        client = client_for(ok_handler(seen))

        asyncio.run(client.fetch_many(["VNM"]))
        asyncio.run(client.fetch("VNM", "1mo"))

        self.assertEqual(len(seen), 1)

    def test_concurrent_misses_fetch_once(self):
        seen = []

        async def handler(request):
            seen.append(request)
            await asyncio.sleep(0.01)
            return httpx.Response(200, json=chart_payload())

        client = client_for(handler)

        async def run():
            return await asyncio.gather(
                client.fetch("VNM", "1mo"), client.fetch("VNM", "1mo")
            )

        await_pair = asyncio.run(run())
        self.assertEqual(len(seen), 1)
        self.assertEqual(await_pair[0].quote, await_pair[1].quote)

    def test_an_expired_entry_is_served_when_the_provider_fails(self):
        seen = []
        failing = {"on": False}

        def handler(request):
            seen.append(request)
            if failing["on"]:
                return httpx.Response(500, json={})
            return httpx.Response(200, json=chart_payload())

        client = client_for(handler, cache_ttl_seconds=0)
        fresh = asyncio.run(client.fetch("VNM", "1mo"))
        failing["on"] = True
        stale = asyncio.run(client.fetch("VNM", "1mo"))

        self.assertEqual(len(seen), 2)
        self.assertTrue(stale.stale)
        self.assertEqual(stale.as_of, fresh.as_of)
        self.assertEqual(stale.quote, fresh.quote)

    def test_a_cold_failure_raises_instead_of_serving_nothing(self):
        def handler(request):
            return httpx.Response(500, json={})

        client = client_for(handler, cache_ttl_seconds=0)
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client.fetch("VNM", "1mo"))
        self.assertEqual(raised.exception.code, "upstream_error")

    def test_fanout_degrades_per_symbol(self):
        def handler(request):
            if "ZZZ" in request.url.path:
                return httpx.Response(404, json={"chart": {"result": None, "error": None}})
            return httpx.Response(200, json=chart_payload())

        rows, _as_of, stale = asyncio.run(
            client_for(handler).fetch_many(["VNM", "ZZZ", "FPT"])
        )

        self.assertEqual([row["symbol"] for row in rows], ["VNM", "ZZZ", "FPT"])
        self.assertIsNone(rows[0]["error"])
        self.assertEqual(rows[1]["error"], "no_data")
        self.assertIsNone(rows[1]["price"])
        self.assertIsNone(rows[2]["error"])
        self.assertFalse(stale)

    def test_fanout_concurrency_stays_bounded(self):
        active = 0
        peak = 0

        async def handler(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(200, json=chart_payload())

        symbols = list(HOSE_LARGE_CAP_SYMBOLS)
        asyncio.run(client_for(handler).fetch_many(symbols))

        self.assertLessEqual(peak, 6)

    def test_a_total_outage_raises_rather_than_rendering_grey_rows(self):
        def handler(request):
            return httpx.Response(500, json={})

        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client_for(handler).fetch_many(["VNM", "FPT"]))
        self.assertEqual(raised.exception.code, "upstream_error")

    def test_the_client_imports_no_write_or_identity_layers(self):
        source = inspect.getsource(market_quotes_module)
        for forbidden in (
            "source.infrastructure",
            "source.security",
            "source.agents",
            "source.tools",
            "control_plane",
            "staging",
        ):
            self.assertNotIn(forbidden, source)


class MarketRouteTests(unittest.TestCase):
    def request(self, permissions=("chat:run",)):
        return SimpleNamespace(
            scope={"user": SimpleNamespace(identity=ACTOR), "auth": list(permissions)}
        )

    def test_routes_are_registered_as_get_only(self):
        for path in ("/v1/market/quotes", "/v1/market/history"):
            route = next(route for route in app.routes if route.path == path)
            self.assertEqual(route.methods, {"GET"})

    def test_quotes_require_a_permission_and_a_real_actor(self):
        for permissions in ((), ("quant:read",)):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(market_quotes(self.request(permissions), Response()))
            self.assertEqual(raised.exception.status_code, 403)

        anonymous = SimpleNamespace(scope={"user": None, "auth": ["chat:run"]})
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(market_quotes(anonymous, Response()))
        self.assertEqual(raised.exception.status_code, 401)

    def test_quotes_default_to_the_whole_display_universe(self):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            return httpx.Response(200, json=chart_payload())

        response = Response()
        with patch(
            "source.http_app._market_quotes_client",
            return_value=client_for(handler),
        ):
            # `symbols=None` is the value FastAPI supplies for the omitted query
            # parameter; calling the handler directly skips that resolution.
            payload = asyncio.run(market_quotes(self.request(), response, symbols=None))

        self.assertEqual(len(payload["quotes"]), len(HOSE_LARGE_CAP_SYMBOLS))
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        self.assertIn("stale", payload)

    def test_quotes_reject_symbols_outside_the_universe(self):
        for value in ("VNM,FAKE", "!!!", "VN", "VNM.VN"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(market_quotes(self.request(), Response(), symbols=value))
            self.assertEqual(raised.exception.status_code, 422, value)

    def test_quotes_normalize_case_and_deduplicate(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=chart_payload())

        with patch(
            "source.http_app._market_quotes_client",
            return_value=client_for(handler),
        ):
            payload = asyncio.run(
                market_quotes(self.request(), Response(), symbols="vnm, VNM")
            )

        self.assertEqual([row["symbol"] for row in payload["quotes"]], ["VNM"])
        self.assertEqual(len(seen), 1)

    def test_history_rejects_symbols_outside_the_universe(self):
        for value in ("FAKE", "ZZZ", "../../etc/passwd", "VN"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    market_history(self.request(), Response(), symbol=value, range_="1mo")
                )
            self.assertEqual(raised.exception.status_code, 422, value)

    def test_history_rejects_an_unsupported_range(self):
        # Reachable only because the tests call the handler directly, which
        # bypasses FastAPI's Query(pattern=...) validation.
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                market_history(self.request(), Response(), symbol="VNM", range_="5y")
            )
        self.assertEqual(raised.exception.status_code, 422)

    def test_history_returns_bars_and_maps_provider_failures(self):
        with patch(
            "source.http_app._market_quotes_client",
            return_value=client_for(ok_handler([])),
        ):
            payload = asyncio.run(
                market_history(self.request(), Response(), symbol="VNM", range_="1mo")
            )
        self.assertEqual(payload["symbol"], "VNM")
        self.assertEqual(payload["range"], "1mo")
        self.assertEqual(len(payload["bars"]), 2)

        def missing(request):
            return httpx.Response(404, json={"chart": {"result": None, "error": None}})

        def broken(request):
            raise httpx.ConnectTimeout("too slow")

        def malformed(request):
            return httpx.Response(200, content=b"not json")

        for handler, expected in (
            (missing, 404),
            (broken, 504),
            (malformed, 502),
        ):
            with patch(
                "source.http_app._market_quotes_client",
                return_value=client_for(handler, cache_ttl_seconds=0),
            ):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        market_history(
                            self.request(), Response(), symbol="VNM", range_="1mo"
                        )
                    )
            self.assertEqual(raised.exception.status_code, expected, handler.__name__)

    def test_routes_touch_no_domain_state(self):
        # The concrete proof that this path writes nothing: the database layer is
        # never even constructed.
        with (
            patch("source.http_app.require_domain_pool") as pool,
            patch(
                "source.http_app._market_quotes_client",
                return_value=client_for(ok_handler([])),
            ),
        ):
            asyncio.run(market_quotes(self.request(), Response(), symbols=None))
            asyncio.run(
                market_history(self.request(), Response(), symbol="VNM", range_="1mo")
            )
        pool.assert_not_called()

    def test_the_routes_are_not_reachable_without_a_session(self):
        # Bootstrap identity is allowlisted by path in source/security/auth.py.
        # A price page must never join that list.
        auth_source = (
            Path(__file__).resolve().parents[1] / "source" / "security" / "auth.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("/v1/market", auth_source)

    def test_the_display_universe_is_well_formed(self):
        self.assertEqual(len(HOSE_LARGE_CAP_SYMBOLS), 27)
        self.assertEqual(len(set(HOSE_LARGE_CAP_SYMBOLS)), 27)
        for symbol in HOSE_LARGE_CAP_SYMBOLS:
            self.assertRegex(symbol, r"^[A-Z0-9]{3,10}$")
