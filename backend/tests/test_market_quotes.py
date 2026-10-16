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
from source.http_app import app, market_history, market_quotes
from source.integrations.market_quotes import MarketQuoteClient, MarketQuoteError

ACTOR = "v1-" + "a" * 64

# SSI sends the chart in nghìn đồng and the board in đồng. These two timestamps
# are 00:00 UTC of 2026-10-12 and 2026-10-13, which is the same calendar day in
# Indochina Time (UTC+7).
BAR_TIMESTAMPS = (1_791_770_400, 1_791_856_800)

BOARD_PATH = "/stock/group/VNALL"
CHART_PATH = "/statistics/charts/history"


def board_row(symbol="AAA", **overrides):
    row = {
        "stockSymbol": symbol,
        "companyNameVi": "CTCP Nhựa An Phát Xanh",
        "companyNameEn": "An Phat Bioplastics JSC",
        "exchange": "hose",
        "stockType": "s",
        "refPrice": 7000,
        "ceiling": 7490,
        "floor": 6510,
        "remainForeignQtty": 12_345_678,
        "tradingUnit": 10,
        "tradingDate": "20261016",
    }
    row.update(overrides)
    return row


def board_payload(rows=None, *, code="SUCCESS"):
    if rows is None:
        rows = [
            board_row("AAA"),
            board_row(
                "ACB",
                companyNameVi="Ngân hàng TMCP Á Châu",
                refPrice=20_600,
                ceiling=22_000,
                floor=19_200,
            ),
            board_row("FPT", companyNameVi="CTCP FPT", refPrice=58_000),
        ]
    return {"code": code, "message": "Get board data success", "data": rows}


def chart_payload(
    *,
    code="SUCCESS",
    closes=(19.25, 19.05),
    timestamps=BAR_TIMESTAMPS,
    volumes=(1_000, 2_000),
    data_override=None,
):
    data = {
        "t": list(timestamps),
        "c": list(closes),
        "o": [],
        "h": [],
        "l": [],
        "v": list(volumes),
        "s": "ok",
        "nextTime": None,
    }
    data.update(data_override or {})
    return {"code": code, "message": "Get chart history data success", "data": data}


def server(*, board=None, chart=None):
    """A transport handler that routes by the two SSI hosts' paths."""
    seen = {"board": [], "chart": []}

    def handler(request):
        if request.url.path == BOARD_PATH:
            seen["board"].append(request)
            if callable(board):
                return board(request)
            return httpx.Response(200, json=board if board is not None else board_payload())
        seen["chart"].append(request)
        if callable(chart):
            return chart(request)
        return httpx.Response(200, json=chart if chart is not None else chart_payload())

    handler.seen = seen
    return handler


def client_for(handler, **kwargs):
    return MarketQuoteClient(
        timeout_seconds=10.0,
        cache_ttl_seconds=kwargs.pop("cache_ttl_seconds", 60),
        universe_ttl_seconds=kwargs.pop("universe_ttl_seconds", 900),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


class MarketBoardTests(unittest.TestCase):
    def test_board_rows_are_normalized_and_trimmed(self):
        client = client_for(server())
        board = asyncio.run(client.fetch_board())

        row = board.rows[0]
        self.assertEqual(row["symbol"], "AAA")
        self.assertEqual(row["name"], "CTCP Nhựa An Phát Xanh")
        # SSI sends the exchange lowercased.
        self.assertEqual(row["exchange"], "HOSE")
        self.assertEqual(row["ref_price"], 7_000.0)
        self.assertEqual(row["ceiling"], 7_490.0)
        self.assertEqual(row["floor"], 6_510.0)
        self.assertEqual(row["foreign_room"], 12_345_678)
        self.assertEqual(row["trading_unit"], 10)
        # `20261016` is normalized so the browser can format it like other dates.
        self.assertEqual(board.trading_date, "2026-10-16")
        self.assertFalse(board.stale)
        self.assertTrue(board.as_of.endswith("+07:00"), board.as_of)
        # The raw board carries far more than the page renders.
        self.assertNotIn("isin", row)
        self.assertNotIn("parValue", row)

    def test_the_whole_board_costs_one_request(self):
        rows = [board_row(f"S{index:03d}") for index in range(312)]
        handler = server(board=board_payload(rows))
        board = asyncio.run(client_for(handler).fetch_board())

        self.assertEqual(len(board.rows), 312)
        self.assertEqual(len(handler.seen["board"]), 1)
        self.assertEqual(handler.seen["board"][0].url.host, "iboard-query.ssi.com.vn")

    def test_board_cache_hit_does_not_refetch(self):
        handler = server()
        client = client_for(handler)

        first = asyncio.run(client.fetch_board())
        second = asyncio.run(client.fetch_board())

        self.assertEqual(len(handler.seen["board"]), 1)
        self.assertEqual(first.rows, second.rows)

    def test_an_expired_board_is_refetched(self):
        handler = server()
        client = client_for(handler, universe_ttl_seconds=0)

        asyncio.run(client.fetch_board())
        asyncio.run(client.fetch_board())

        self.assertEqual(len(handler.seen["board"]), 2)

    def test_an_expired_board_is_served_when_the_provider_fails(self):
        failing = {"on": False}

        def board(request):
            if failing["on"]:
                return httpx.Response(500, json={})
            return httpx.Response(200, json=board_payload())

        handler = server(board=board)
        client = client_for(handler, universe_ttl_seconds=0)
        fresh = asyncio.run(client.fetch_board())
        failing["on"] = True
        stale = asyncio.run(client.fetch_board())

        self.assertEqual(len(handler.seen["board"]), 2)
        self.assertTrue(stale.stale)
        self.assertEqual(stale.rows, fresh.rows)
        self.assertEqual(stale.trading_date, fresh.trading_date)

    def test_a_cold_board_failure_raises_instead_of_serving_nothing(self):
        handler = server(board=lambda request: httpx.Response(500, json={}))
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client_for(handler).fetch_board())
        self.assertEqual(raised.exception.code, "upstream_error")

    def test_a_provider_error_envelope_maps_to_upstream_error(self):
        # SSI answers an unknown path or a missing parameter with this envelope
        # and a 4xx status, so the code is checked as well as the status.
        handler = server(
            board=lambda request: httpx.Response(
                200, json={"code": "ERR_SQ_0001", "message": "Request not found"}
            )
        )
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client_for(handler).fetch_board())
        self.assertEqual(raised.exception.code, "upstream_error")
        self.assertIn("ERR_SQ_0001", raised.exception.message)

    def test_a_provider_code_is_only_echoed_when_it_looks_like_a_code(self):
        handler = server(
            board=lambda request: httpx.Response(
                200, json={"code": "<script>alert(1)</script>", "data": []}
            )
        )
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client_for(handler).fetch_board())
        self.assertNotIn("<script>", raised.exception.message)

    def test_a_row_without_prices_is_kept_with_nulls(self):
        # One listing without a published band must not blank the other 311.
        handler = server(board=board_payload([board_row(refPrice=None), board_row("ACB")]))
        board = asyncio.run(client_for(handler).fetch_board())

        self.assertEqual([row["symbol"] for row in board.rows], ["AAA", "ACB"])
        self.assertIsNone(board.rows[0]["ref_price"])
        self.assertEqual(board.rows[1]["ref_price"], 7_000.0)

    def test_entries_that_are_not_hose_shares_are_skipped(self):
        # VNALL is all HOSE shares today, but the page promises HOSE, so a
        # warrant or another exchange appearing in the group is out of scope.
        handler = server(
            board=board_payload(
                [
                    board_row("AAA"),
                    board_row("BBB", exchange="hnx"),
                    board_row("CCC", stockType="w"),
                    board_row("DDD"),
                ]
            )
        )
        board = asyncio.run(client_for(handler).fetch_board())

        self.assertEqual([row["symbol"] for row in board.rows], ["AAA", "DDD"])

    def test_an_entry_without_an_exchange_or_type_is_still_kept(self):
        # Absent is not the same as disqualifying: an unnamed exchange must not
        # be able to silently empty the board.
        handler = server(
            board=board_payload([board_row(exchange=None, stockType=None)])
        )
        board = asyncio.run(client_for(handler).fetch_board())

        self.assertEqual(len(board.rows), 1)
        self.assertIsNone(board.rows[0]["exchange"])

    def test_a_malformed_or_duplicated_symbol_is_a_bad_payload(self):
        for rows in (
            [board_row("VN")],
            [board_row("AAA"), board_row("AAA")],
            [board_row("AAA"), "not a row"],
        ):
            handler = server(board=board_payload(rows))
            with self.assertRaises(MarketQuoteError) as raised:
                asyncio.run(client_for(handler).fetch_board())
            self.assertEqual(raised.exception.code, "bad_payload", rows)

    def test_an_empty_or_shapeless_board_is_a_bad_payload(self):
        for payload in ({"code": "SUCCESS", "data": []}, {"code": "SUCCESS"}, []):
            handler = server(board=payload)
            with self.assertRaises(MarketQuoteError) as raised:
                asyncio.run(client_for(handler).fetch_board())
            self.assertEqual(raised.exception.code, "bad_payload", payload)

    def test_a_row_without_a_trading_date_is_tolerated(self):
        # The session date is informational; the prices are not.
        handler = server(board=board_payload([board_row(tradingDate=None)]))
        board = asyncio.run(client_for(handler).fetch_board())

        self.assertEqual(len(board.rows), 1)
        self.assertIsNone(board.trading_date)


class MarketChartTests(unittest.TestCase):
    def test_chart_prices_are_converted_from_thousands_to_dong(self):
        # SSI quotes the chart in nghìn đồng. Verified against the board: SSI's
        # chart close of 19.25 is its board refPrice of 19,250.
        handler = server(chart=chart_payload(closes=(19.25, 20.6)))
        snapshot = asyncio.run(client_for(handler).fetch("SSI", "1mo"))

        self.assertEqual([bar["close"] for bar in snapshot.bars], [19_250.0, 20_600.0])
        for bar in snapshot.bars:
            self.assertIsInstance(bar["close"], float)

    def test_bars_carry_the_ict_trading_date(self):
        snapshot = asyncio.run(client_for(server()).fetch("SSI", "1mo"))

        self.assertEqual(
            [bar["time"] for bar in snapshot.bars], ["2026-10-12", "2026-10-13"]
        )
        self.assertEqual(snapshot.bars[1]["volume"], 2_000)
        self.assertFalse(snapshot.stale)

    def test_the_chart_request_asks_for_a_forward_daily_window(self):
        handler = server()
        asyncio.run(client_for(handler).fetch("SSI", "3mo"))

        request = handler.seen["chart"][0]
        self.assertEqual(request.url.host, "iboard-api.ssi.com.vn")
        self.assertEqual(request.url.params["resolution"], "1D")
        self.assertEqual(request.url.params["symbol"], "SSI")
        # A reversed window answers 200 with zero bars, so the order matters.
        self.assertLess(int(request.url.params["from"]), int(request.url.params["to"]))

    def test_an_empty_series_is_no_data(self):
        # SSI answers HTTP 200 with code SUCCESS and empty arrays for a symbol it
        # does not trade, so the status code alone cannot detect it.
        handler = server(chart=chart_payload(closes=(), timestamps=(), volumes=()))
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client_for(handler).fetch("ZZZZ", "1mo"))
        self.assertEqual(raised.exception.code, "no_data")

    def test_null_closes_are_dropped(self):
        handler = server(
            chart=chart_payload(
                closes=(19.4, 19.05, None),
                timestamps=(*BAR_TIMESTAMPS, 1_791_943_200),
                volumes=(10, 20, None),
            )
        )
        snapshot = asyncio.run(client_for(handler).fetch("SSI", "1mo"))

        self.assertEqual(len(snapshot.bars), 2)
        self.assertEqual(snapshot.bars[-1]["close"], 19_050.0)

    def test_mismatched_close_and_timestamp_arrays_are_a_bad_payload(self):
        handler = server(chart=chart_payload(closes=(19.05,), timestamps=BAR_TIMESTAMPS))
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client_for(handler).fetch("SSI", "1mo"))
        self.assertEqual(raised.exception.code, "bad_payload")

    def test_a_chart_without_a_data_object_is_a_bad_payload(self):
        for payload in ({"code": "SUCCESS"}, {"code": "SUCCESS", "data": []}, []):
            handler = server(chart=payload)
            with self.assertRaises(MarketQuoteError) as raised:
                asyncio.run(client_for(handler).fetch("SSI", "1mo"))
            self.assertEqual(raised.exception.code, "bad_payload", payload)

    def test_timeout_and_transport_failures_map_to_their_codes(self):
        def timeout_handler(request):
            raise httpx.ConnectTimeout("too slow")

        def connect_handler(request):
            raise httpx.ConnectError("refused")

        with self.assertRaises(Exception) as timed_out:
            asyncio.run(client_for(server(chart=timeout_handler)).fetch("SSI", "1mo"))
        self.assertEqual(timed_out.exception.code, "timeout")

        with self.assertRaises(Exception) as unreachable:
            asyncio.run(client_for(server(chart=connect_handler)).fetch("SSI", "1mo"))
        self.assertEqual(unreachable.exception.code, "upstream_error")

    def test_non_json_body_is_a_bad_payload(self):
        handler = server(chart=lambda request: httpx.Response(200, content=b"<html>nope"))
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client_for(handler).fetch("SSI", "1mo"))
        self.assertEqual(raised.exception.code, "bad_payload")

    def test_rate_limiting_is_mapped_and_not_retried(self):
        handler = server(chart=lambda request: httpx.Response(429, json={"error": "slow"}))
        with self.assertRaises(MarketQuoteError) as raised:
            asyncio.run(client_for(handler).fetch("SSI", "1mo"))
        self.assertEqual(raised.exception.code, "upstream_error")
        self.assertEqual(len(handler.seen["chart"]), 1)

    def test_chart_cache_hit_does_not_refetch(self):
        handler = server()
        client = client_for(handler)

        first = asyncio.run(client.fetch("SSI", "1mo"))
        second = asyncio.run(client.fetch("SSI", "1mo"))

        self.assertEqual(len(handler.seen["chart"]), 1)
        self.assertEqual(first.bars, second.bars)

    def test_concurrent_chart_misses_fetch_once(self):
        seen = []

        async def chart(request):
            seen.append(request)
            await asyncio.sleep(0.01)
            return httpx.Response(200, json=chart_payload())

        client = client_for(server(chart=chart))

        async def run():
            return await asyncio.gather(
                client.fetch("SSI", "1mo"), client.fetch("SSI", "1mo")
            )

        both = asyncio.run(run())
        self.assertEqual(len(seen), 1)
        self.assertEqual(both[0].bars, both[1].bars)

    def test_a_different_range_is_a_different_cache_entry(self):
        handler = server()
        client = client_for(handler)

        asyncio.run(client.fetch("SSI", "1mo"))
        asyncio.run(client.fetch("SSI", "3mo"))

        self.assertEqual(len(handler.seen["chart"]), 2)

    def test_no_internal_identity_headers_are_sent(self):
        # The provider is a third party: an actor pseudonym or thread ID would be
        # an identifier leak. This is why the client does not subclass
        # WorkerHttpClient, which injects both.
        handler = server()
        asyncio.run(client_for(handler).fetch_board())

        headers = handler.seen["board"][0].headers
        self.assertNotIn("authorization", headers)
        self.assertNotIn("x-nexus-actor-key", headers)
        self.assertNotIn("x-nexus-thread-id", headers)
        self.assertTrue(headers["user-agent"])

    def test_the_client_imports_no_write_or_identity_layers(self):
        source = inspect.getsource(market_quotes_module)
        for forbidden in (
            "source.infrastructure",
            "source.security",
            "source.agents",
            "source.tools",
            "control_plane",
            "staging",
            # The previous provider, pinned so no remnant of it survives.
            "yahoo",
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

    def test_the_quotes_route_takes_no_symbol_parameter(self):
        # The board is the whole provider universe. A caller-supplied symbol list
        # would turn an authenticated route into a proxy for a market-data API,
        # which is the boundary the governed pipeline exists to keep.
        parameters = inspect.signature(market_quotes).parameters
        self.assertEqual(set(parameters) - {"request", "response"}, set())

    def test_quotes_require_a_permission_and_a_real_actor(self):
        for permissions in ((), ("quant:read",)):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(market_quotes(self.request(permissions), Response()))
            self.assertEqual(raised.exception.status_code, 403)

        anonymous = SimpleNamespace(scope={"user": None, "auth": ["chat:run"]})
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(market_quotes(anonymous, Response()))
        self.assertEqual(raised.exception.status_code, 401)

    def test_quotes_return_the_whole_provider_board(self):
        rows = [board_row(f"S{index:03d}") for index in range(312)]
        handler = server(board=board_payload(rows))
        response = Response()
        with patch(
            "source.http_app._market_quotes_client", return_value=client_for(handler)
        ):
            payload = asyncio.run(market_quotes(self.request(), response))

        self.assertEqual(len(payload["quotes"]), 312)
        self.assertEqual(payload["trading_date"], "2026-10-16")
        self.assertIn("stale", payload)
        self.assertEqual(response.headers["cache-control"], "no-store, private")

    def test_quotes_map_a_provider_outage_to_502(self):
        handler = server(board=lambda request: httpx.Response(500, json={}))
        with patch(
            "source.http_app._market_quotes_client", return_value=client_for(handler)
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(market_quotes(self.request(), Response()))
        self.assertEqual(raised.exception.status_code, 502)

    def test_history_takes_membership_from_the_provider_board(self):
        handler = server()
        with patch(
            "source.http_app._market_quotes_client", return_value=client_for(handler)
        ):
            payload = asyncio.run(
                market_history(self.request(), Response(), symbol="fpt", range_="1mo")
            )

        # Lowercased by the handler, accepted because the board lists it.
        self.assertEqual(payload["symbol"], "FPT")
        self.assertEqual(payload["currency"], "VND")
        self.assertEqual(payload["quote"]["ref_price"], 58_000.0)

    def test_history_rejects_a_symbol_the_board_does_not_list(self):
        handler = server()
        with patch(
            "source.http_app._market_quotes_client", return_value=client_for(handler)
        ):
            for value in ("FAKE", "ZZZ", "VN"):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        market_history(
                            self.request(), Response(), symbol=value, range_="1mo"
                        )
                    )
                self.assertEqual(raised.exception.status_code, 422, value)

        # Rejected from the cached board, so the chart endpoint was never asked
        # to look up an arbitrary symbol.
        self.assertEqual(handler.seen["chart"], [])

    def test_history_rejects_an_unsupported_range(self):
        # Reachable only because the tests call the handler directly, which
        # bypasses FastAPI's Query(pattern=...) validation.
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                market_history(self.request(), Response(), symbol="FPT", range_="5y")
            )
        self.assertEqual(raised.exception.status_code, 422)

    def test_history_returns_bars_and_maps_provider_failures(self):
        with patch(
            "source.http_app._market_quotes_client", return_value=client_for(server())
        ):
            payload = asyncio.run(
                market_history(self.request(), Response(), symbol="FPT", range_="1mo")
            )
        self.assertEqual(payload["range"], "1mo")
        self.assertEqual(len(payload["bars"]), 2)
        self.assertEqual(payload["bars"][0]["close"], 19_250.0)

        cases = (
            # An empty series is SSI's unknown-symbol answer, not a 404.
            (server(chart=chart_payload(closes=(), timestamps=(), volumes=())), 404),
            (server(chart=lambda request: httpx.Response(500, json={})), 502),
            (server(board=lambda request: httpx.Response(500, json={})), 502),
            (server(chart=lambda request: httpx.Response(200, content=b"nope")), 502),
        )
        for handler, expected in cases:
            with patch(
                "source.http_app._market_quotes_client",
                return_value=client_for(handler, cache_ttl_seconds=0),
            ):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        market_history(
                            self.request(), Response(), symbol="FPT", range_="1mo"
                        )
                    )
            self.assertEqual(raised.exception.status_code, expected)

    def test_routes_touch_no_domain_state(self):
        # The concrete proof that this path writes nothing: the database layer is
        # never even constructed.
        with (
            patch("source.http_app.require_domain_pool") as pool,
            patch(
                "source.http_app._market_quotes_client",
                return_value=client_for(server()),
            ),
        ):
            asyncio.run(market_quotes(self.request(), Response()))
            asyncio.run(
                market_history(self.request(), Response(), symbol="FPT", range_="1mo")
            )
        pool.assert_not_called()

    def test_the_routes_are_not_reachable_without_a_session(self):
        # Bootstrap identity is allowlisted by path in source/security/auth.py.
        # A price page must never join that list.
        auth_source = (
            Path(__file__).resolve().parents[1] / "source" / "security" / "auth.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("/v1/market", auth_source)

    def test_no_repo_symbol_list_remains(self):
        # The universe is the provider's board, not a constant that silently
        # drifts from the market it claims to describe.
        self.assertFalse(
            (Path(__file__).resolve().parents[1] / "source" / "core"
             / "market_universe.py").exists()
        )
