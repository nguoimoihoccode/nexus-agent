from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from worker.app import create_app
from worker.daemon import build_refresh_request, refresh_market_news_once
from worker.models import MarketRefreshRequest
from worker.service import AiTraderWorker


class AiTraderWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.worker = AiTraderWorker(Path(self.tmpdir.name) / "clawtrader.db")
        self.client = TestClient(
            create_app(self.worker, token_provider=lambda: "secret-token")
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_health_is_available(self) -> None:
        health = self.client.get("/health")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["worker"], "ai-trader-worker")
        self.assertEqual(health.json()["status"], "ok")

    def test_readiness_preserves_public_contract_status(self) -> None:
        readiness = self.client.get(
            "/ready",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(readiness.json()["status"], "ready")
        self.assertTrue(readiness.json()["dependencies_ready"])

    def test_request_correlation_is_returned(self) -> None:
        response = self.client.get(
            "/health",
            headers={"X-Request-ID": "req-test-1"},
        )

        self.assertEqual(response.headers["X-Request-ID"], "req-test-1")

    def test_market_intel_returns_stable_fallback_without_snapshots(self) -> None:
        overview = self.client.get("/api/market-intel/overview").json()
        news = self.client.get("/api/market-intel/news", params={"limit": 2}).json()
        macro = self.client.get(
            "/api/market-intel/news",
            params={"category": "macro", "limit": 2},
        ).json()

        self.assertFalse(overview["available"])
        self.assertEqual(overview["news_status"], "quiet")
        self.assertEqual(overview["headline_count"], 0)
        self.assertFalse(news["available"])
        self.assertEqual(news["total_items"], 0)
        self.assertTrue(news["stale"])
        self.assertIsNone(news["next_refresh_at"])
        self.assertEqual(news["refresh_interval_seconds"], 3600)
        self.assertEqual(len(news["categories"]), 4)
        self.assertEqual([section["category"] for section in macro["categories"]], ["macro"])

    def test_market_refresh_requires_matching_shared_token(self) -> None:
        missing = self.client.post("/api/market-intel/refresh", json={})
        wrong = self.client.post(
            "/api/market-intel/refresh",
            headers={"X-Claw-Token": "wrong"},
            json={},
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)

    def test_market_refresh_requires_alpha_vantage_key(self) -> None:
        response = self.client.post(
            "/api/market-intel/refresh",
            headers={"Authorization": "Bearer secret-token"},
            json={},
        )

        self.assertEqual(response.status_code, 503)

    def test_production_rejects_non_https_or_unlisted_provider(self) -> None:
        with patch.dict("os.environ", {"NEXUS_ENV": "production"}, clear=False):
            with self.assertRaisesRegex(ValueError, "must use HTTPS"):
                AiTraderWorker(
                    Path(self.tmpdir.name) / "http-provider.db",
                    alpha_vantage_base_url="http://www.alphavantage.co/query",
                )
            with self.assertRaisesRegex(ValueError, "not allowlisted"):
                AiTraderWorker(
                    Path(self.tmpdir.name) / "other-provider.db",
                    alpha_vantage_base_url="https://example.com/query",
                )

    def test_production_accepts_explicit_provider_allowlist(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "NEXUS_ENV": "production",
                "ALPHA_VANTAGE_ALLOWED_HOSTS": "provider.example",
            },
            clear=False,
        ):
            worker = AiTraderWorker(
                Path(self.tmpdir.name) / "allowlisted-provider.db",
                alpha_vantage_base_url="https://provider.example/query",
            )

        self.assertEqual(worker.alpha_vantage_base_url, "https://provider.example/query")

    def test_market_refresh_writes_news_and_overview_snapshots(self) -> None:
        calls = []

        def fake_fetcher(category, params):
            calls.append((category, params))
            return _alpha_vantage_payload(
                title="Apple shares rise after earnings beat",
                ticker="AAPL",
                source="Reuters",
            )

        worker = AiTraderWorker(
            Path(self.tmpdir.name) / "market-refresh.db",
            alpha_vantage_api_key="alpha-key",
            market_news_fetcher=fake_fetcher,
        )
        client = TestClient(create_app(worker, token_provider=lambda: "secret-token"))

        refresh = client.post(
            "/api/market-intel/refresh",
            headers={"Authorization": "Bearer secret-token"},
            json={"categories": ["equities"], "lookback_hours": 24, "limit": 10},
        )
        news = client.get("/api/market-intel/news", params={"category": "equities"}).json()
        all_news = client.get("/api/market-intel/news").json()
        overview = client.get("/api/market-intel/overview").json()

        self.assertEqual(refresh.status_code, 200)
        self.assertTrue(refresh.json()["success"])
        self.assertEqual(refresh.json()["categories"][0]["item_count"], 1)
        self.assertEqual(calls[0][0], "equities")
        self.assertEqual(calls[0][1]["function"], "NEWS_SENTIMENT")
        self.assertEqual(calls[0][1]["topics"], "financial_markets")
        self.assertEqual(calls[0][1]["sort"], "LATEST")
        self.assertEqual(calls[0][1]["limit"], 10)
        self.assertEqual(calls[0][1]["apikey"], "alpha-key")
        self.assertTrue(news["available"])
        self.assertEqual(news["categories"][0]["items"][0]["title"], "Apple shares rise after earnings beat")
        self.assertEqual(news["categories"][0]["items"][0]["time_published"], "2026-07-09T12:30:00Z")
        self.assertEqual(news["categories"][0]["items"][0]["ticker_sentiment"][0]["ticker"], "AAPL")
        self.assertTrue(all_news["available"])
        self.assertEqual(all_news["total_items"], 1)
        self.assertTrue(overview["available"])
        self.assertFalse(overview["stale"])
        self.assertIsNotNone(overview["next_refresh_at"])
        self.assertEqual(overview["headline_count"], 1)
        self.assertEqual(overview["latest_headline"], "Apple shares rise after earnings beat")
        self.assertEqual(overview["top_source"], "Reuters")
        self.assertEqual(overview["news_status"], "calm")

    def test_daemon_refresh_once_writes_market_snapshots(self) -> None:
        calls = []

        def fake_fetcher(category, params):
            calls.append((category, params))
            return _alpha_vantage_payload(
                title="Macro data lifts market mood",
                ticker="SPY",
                source="CNBC",
            )

        worker = AiTraderWorker(
            Path(self.tmpdir.name) / "daemon-refresh.db",
            alpha_vantage_api_key="alpha-key",
            market_news_fetcher=fake_fetcher,
        )

        result = refresh_market_news_once(
            worker=worker,
            env={
                "AI_TRADER_MARKET_NEWS_CATEGORIES": "macro",
                "AI_TRADER_MARKET_NEWS_LOOKBACK_HOURS": "12",
                "AI_TRADER_MARKET_NEWS_LIMIT": "4",
            },
        )
        news = worker.market_news(category="macro")

        self.assertTrue(result["success"])
        self.assertEqual(calls[0][0], "macro")
        self.assertEqual(calls[0][1]["limit"], 4)
        self.assertEqual(calls[0][1]["topics"], "economy_macro")
        self.assertTrue(news["available"])
        self.assertFalse(news["stale"])

    def test_daemon_refresh_request_reads_env_knobs(self) -> None:
        request = build_refresh_request(
            {
                "AI_TRADER_MARKET_NEWS_CATEGORIES": "equities,crypto",
                "AI_TRADER_MARKET_NEWS_LOOKBACK_HOURS": "6",
                "AI_TRADER_MARKET_NEWS_LIMIT": "8",
            }
        )

        self.assertEqual(request.categories, ["equities", "crypto"])
        self.assertEqual(request.lookback_hours, 6)
        self.assertEqual(request.limit, 8)

    def test_market_news_filters_by_ticker_sentiment_symbols(self) -> None:
        def fake_fetcher(category, params):
            return _alpha_vantage_payload_many(
                [
                    ("Apple supply chain update", "AAPL", "Reuters"),
                    ("Datacenter demand lifts Dell", "DELL", "AP"),
                    ("Microsoft cloud spending steady", "MSFT", "CNBC"),
                ]
            )

        worker = AiTraderWorker(
            Path(self.tmpdir.name) / "market-symbol-filter.db",
            alpha_vantage_api_key="alpha-key",
            market_news_fetcher=fake_fetcher,
        )
        worker.refresh_market_intel(MarketRefreshRequest(categories=["equities"]))

        news = worker.market_news(
            category="equities",
            symbols="AAPL,MSFT,NVDA",
            limit=5,
        )
        missing = worker.market_news(category="equities", symbols="NVDA", limit=5)

        self.assertTrue(news["filtered"])
        self.assertEqual(news["symbols_filter"], ["AAPL", "MSFT", "NVDA"])
        self.assertEqual(news["matched_symbols"], ["AAPL", "MSFT"])
        self.assertEqual(news["unmatched_symbols"], ["NVDA"])
        self.assertEqual(news["total_items"], 2)
        self.assertTrue(news["available"])
        self.assertEqual(
            [item["title"] for item in news["categories"][0]["items"]],
            ["Apple supply chain update", "Microsoft cloud spending steady"],
        )
        self.assertFalse(missing["available"])
        self.assertEqual(missing["total_items"], 0)
        self.assertEqual(missing["matched_symbols"], [])
        self.assertEqual(missing["unmatched_symbols"], ["NVDA"])

    def test_market_refresh_reports_provider_errors_per_category(self) -> None:
        def fake_fetcher(category, params):
            if category == "macro":
                return {"Note": "rate limit"}
            if category == "crypto":
                return {"Information": "temporary information"}
            raise RuntimeError("Alpha Vantage request timed out")

        worker = AiTraderWorker(
            Path(self.tmpdir.name) / "market-errors.db",
            alpha_vantage_api_key="alpha-key",
            market_news_fetcher=fake_fetcher,
        )
        client = TestClient(create_app(worker, token_provider=lambda: "secret-token"))

        response = client.post(
            "/api/market-intel/refresh",
            headers={"X-Claw-Token": "secret-token"},
            json={"categories": ["macro", "crypto", "commodities"]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["category"] for item in payload["categories"]], ["macro", "crypto", "commodities"])
        self.assertTrue(all(item["error"] for item in payload["categories"]))
        self.assertFalse(payload["overview"]["available"])

    def test_market_refresh_invalid_payload_does_not_replace_existing_snapshot(self) -> None:
        responses = [
            _alpha_vantage_payload(title="Durable headline", ticker="MSFT", source="AP"),
            {"feed": {"not": "a-list"}},
        ]

        def fake_fetcher(category, params):
            return responses.pop(0)

        worker = AiTraderWorker(
            Path(self.tmpdir.name) / "market-stale.db",
            alpha_vantage_api_key="alpha-key",
            market_news_fetcher=fake_fetcher,
        )
        client = TestClient(create_app(worker, token_provider=lambda: "secret-token"))
        headers = {"Authorization": "Bearer secret-token"}

        first = client.post(
            "/api/market-intel/refresh",
            headers=headers,
            json={"categories": ["equities"]},
        )
        second = client.post(
            "/api/market-intel/refresh",
            headers=headers,
            json={"categories": ["equities"]},
        )
        news = client.get("/api/market-intel/news", params={"category": "equities"}).json()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["categories"][0]["error"])
        self.assertEqual(news["categories"][0]["items"][0]["title"], "Durable headline")

    def test_publish_requires_matching_shared_token(self) -> None:
        request = {
            "market": "us-stock",
            "title": "Nexus research",
            "content": "Research only.",
        }

        missing = self.client.post("/api/signals/strategy", json=request)
        wrong = self.client.post(
            "/api/signals/strategy",
            headers={"X-Claw-Token": "wrong"},
            json=request,
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)

    def test_protected_endpoints_fail_closed_when_token_is_unconfigured(self) -> None:
        client = TestClient(create_app(self.worker, token_provider=lambda: ""))

        response = client.post(
            "/api/signals/strategy",
            headers={"Authorization": "Bearer anything"},
            json={
                "market": "us-stock",
                "title": "Nexus research",
                "content": "Research only.",
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "AI_TRADER_TOKEN is not configured.")

    def test_publish_rejects_oversized_content(self) -> None:
        response = self.client.post(
            "/api/signals/strategy",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "market": "us-stock",
                "title": "Nexus research",
                "content": "x" * 20001,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_publish_strategy_discussion_and_filter_feed(self) -> None:
        strategy = self.client.post(
            "/api/signals/strategy",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "market": "us-stock",
                "title": "Nexus strategy",
                "content": "Research only.",
                "symbols": "AAPL,MSFT",
                "tags": "qlib,research",
            },
        )
        discussion = self.client.post(
            "/api/signals/discussion",
            headers={"X-Claw-Token": "secret-token"},
            json={
                "market": "crypto",
                "symbol": "BTC",
                "title": "Nexus discussion",
                "content": "A cautious note.",
                "tags": ["macro", "risk"],
            },
        )

        self.assertEqual(strategy.status_code, 200)
        self.assertTrue(strategy.json()["success"])
        self.assertEqual(discussion.status_code, 200)
        self.assertTrue(discussion.json()["success"])

        all_feed = self.client.get("/api/signals/feed", params={"limit": 10}).json()
        strategy_feed = self.client.get(
            "/api/signals/feed",
            params={"message_type": "strategy"},
        ).json()
        keyword_feed = self.client.get(
            "/api/signals/feed",
            params={"keyword": "cautious"},
        ).json()

        self.assertEqual(all_feed["total"], 2)
        self.assertEqual(strategy_feed["total"], 1)
        self.assertEqual(strategy_feed["signals"][0]["symbols"], ["AAPL", "MSFT"])
        self.assertEqual(strategy_feed["signals"][0]["tags"], ["qlib", "research"])
        self.assertEqual(keyword_feed["total"], 1)
        self.assertEqual(keyword_feed["signals"][0]["symbol"], "BTC")

    def test_publication_idempotency_survives_worker_restart(self) -> None:
        request = {
            "market": "us-stock",
            "title": "Nexus strategy",
            "content": "Research only.",
            "actor_key": "v1-" + ("a" * 64),
            "idempotency_key": "publish:test:one",
            "action_digest": "act_v1_" + ("b" * 64),
        }
        headers = {"Authorization": "Bearer secret-token"}

        first = self.client.post("/api/signals/strategy", headers=headers, json=request)
        restarted = TestClient(
            create_app(
                AiTraderWorker(Path(self.tmpdir.name) / "clawtrader.db"),
                token_provider=lambda: "secret-token",
            )
        )
        retried = restarted.post(
            "/api/signals/strategy",
            headers=headers,
            json=request,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["signal_id"], first.json()["signal_id"])
        self.assertTrue(retried.json()["reused"])

    def test_publication_idempotency_rejects_changed_action(self) -> None:
        headers = {"Authorization": "Bearer secret-token"}
        request = {
            "market": "us-stock",
            "title": "Nexus strategy",
            "content": "Research only.",
            "actor_key": "v1-" + ("a" * 64),
            "idempotency_key": "publish:test:conflict",
            "action_digest": "act_v1_" + ("b" * 64),
        }
        first = self.client.post("/api/signals/strategy", headers=headers, json=request)
        request["action_digest"] = "act_v1_" + ("c" * 64)

        conflict = self.client.post(
            "/api/signals/strategy",
            headers=headers,
            json=request,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json()["detail"],
            "Publication idempotency key conflicts with another action.",
        )

    def test_signal_feed_treats_sql_injection_keyword_as_data(self) -> None:
        self.client.post(
            "/api/signals/strategy",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "market": "us-stock",
                "title": "Nexus strategy",
                "content": "Research only.",
            },
        )
        self.client.post(
            "/api/signals/discussion",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "market": "crypto",
                "symbol": "BTC",
                "title": "Nexus discussion",
                "content": "A cautious note.",
            },
        )

        response = self.client.get(
            "/api/signals/feed",
            params={"keyword": "%' OR 1=1 --"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 0)

    def test_publish_preserves_html_payload_as_plain_signal_data(self) -> None:
        payload = "<script>alert(1)</script><b>Research only.</b>"
        response = self.client.post(
            "/api/signals/discussion",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "market": "us-stock",
                "symbol": "AAPL",
                "title": "HTML payload",
                "content": payload,
            },
        )
        feed = self.client.get("/api/signals/feed", params={"keyword": "alert"}).json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(feed["total"], 1)
        self.assertEqual(feed["signals"][0]["content"], payload)

    def test_heartbeat_requires_auth_and_returns_empty_payload(self) -> None:
        missing = self.client.post("/api/claw/agents/heartbeat")
        ok = self.client.post(
            "/api/claw/agents/heartbeat",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(ok.status_code, 200)
        payload = ok.json()
        self.assertEqual(payload["messages"], [])
        self.assertEqual(payload["tasks"], [])
        self.assertEqual(payload["message_count"], 0)
        self.assertEqual(payload["task_count"], 0)
        self.assertEqual(payload["recommended_poll_interval_seconds"], 30)

def _alpha_vantage_payload(*, title: str, ticker: str, source: str) -> dict:
    return _alpha_vantage_payload_many([(title, ticker, source)])


def _alpha_vantage_payload_many(items: list[tuple[str, str, str]]) -> dict:
    return {
        "feed": [
            _alpha_vantage_item(title=title, ticker=ticker, source=source)
            for title, ticker, source in items
        ]
    }


def _alpha_vantage_item(*, title: str, ticker: str, source: str) -> dict:
    return {
        "title": title,
        "url": f"https://example.com/{ticker.lower()}",
        "source": source,
        "summary": "A compact market summary.",
        "banner_image": None,
        "time_published": "20260709T123000",
        "overall_sentiment_score": "0.31",
        "overall_sentiment_label": "Bullish",
        "ticker_sentiment": [
            {
                "ticker": ticker,
                "relevance_score": "0.91",
                "ticker_sentiment_score": "0.42",
                "ticker_sentiment_label": "Bullish",
            }
        ],
        "topics": [
            {
                "topic": "Financial Markets",
                "relevance_score": "0.74",
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
