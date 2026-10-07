"""Focused tests for Qlib worker contracts and quant harness wiring."""

import asyncio
import json
import unittest
from uuid import UUID
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables.config import set_config_context

from source.agents.config import AssignedSkillsMiddleware
from source.agents.approval_middleware import GovernedApprovalMiddleware
from source.agents.sub_agents import ai_trader_agent, all_subagents
from source.core.config import settings
from source.core.observability import correlation_headers
from source.integrations.ai_trader_client import AiTraderClient, AiTraderError
from source.tools.custom import DelegationLimitMiddleware, ToolExecutionPolicy
from source.integrations.qlib_client import QlibClient, QuantWorkerError
from source.integrations.quant_data_client import QuantDataClient
from source.tools.ai_trader import AiTraderTools
from source.tools.quant_data import QuantDataTools
from source.tools.quant import QuantTools

LIMITATIONS = {
    "point_in_time_status": "unsupported",
    "survivorship_status": "user_supplied",
    "adjustment_status": {
        "requested_policy": "auto",
        "effective_policy": "splits_and_dividends",
        "verification_status": "unsupported",
    },
    "calendar_status": "inferred_union",
    "missing_data_policy": "no_fill_per_symbol",
    "provider_revision_status": "unsupported",
    "production_eligibility": "research_only",
}


class QlibClientTests(unittest.TestCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_correlation_headers_include_active_graph_context(self):
        with set_config_context(
            {
                "configurable": {"thread_id": "thread-123"},
                "run_id": UUID("12345678-1234-5678-1234-567812345678"),
            }
        ) as context:
            headers = context.run(correlation_headers)

        self.assertEqual(headers["X-Nexus-Thread-ID"], "thread-123")
        self.assertEqual(
            headers["X-Nexus-Run-ID"],
            "12345678-1234-5678-1234-567812345678",
        )
        self.assertIn("X-Request-ID", headers)

    def test_success_response(self):
        async def handler(request):
            self.assertIn("x-request-id", request.headers)
            return httpx.Response(
                200,
                json=[
                    {
                        "dataset_id": "openbb-demo",
                        "universe": "demo",
                        "ready": True,
                        "limitations": LIMITATIONS,
                        "production_eligibility": "research_only",
                    }
                ],
            )

        client = QlibClient(
            "http://quant-worker",
            transport=httpx.MockTransport(handler),
        )
        result = self.run_async(client.list_datasets())
        self.assertEqual(result[0]["dataset_id"], "openbb-demo")

    def test_pending_result_is_preserved(self):
        async def handler(request):
            return httpx.Response(
                200, json={"experiment_id": "exp_1", "status": "running", "error": None}
            )

        client = QlibClient(
            "http://quant-worker",
            transport=httpx.MockTransport(handler),
        )
        result = self.run_async(client.get_result("exp_1"))
        self.assertEqual(result["status"], "running")

    def test_timeout_has_stable_code(self):
        async def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        client = QlibClient(
            "http://quant-worker",
            get_retries=0,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(QuantWorkerError) as raised:
            self.run_async(client.list_datasets())
        self.assertEqual(raised.exception.code, "quant_worker_timeout")

    def test_invalid_json_has_stable_code(self):
        async def handler(request):
            return httpx.Response(200, text="not-json")

        client = QlibClient(
            "http://quant-worker",
            get_retries=0,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(QuantWorkerError) as raised:
            self.run_async(client.list_datasets())
        self.assertEqual(raised.exception.code, "quant_worker_unavailable")

    def test_contract_violation_has_stable_code(self):
        async def handler(request):
            return httpx.Response(200, json=[{"dataset_id": "missing-fields"}])

        client = QlibClient(
            "http://quant-worker",
            get_retries=0,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(QuantWorkerError) as raised:
            self.run_async(client.list_datasets())

        self.assertEqual(raised.exception.code, "invalid_worker_response")
        self.assertEqual(raised.exception.stage, "contract")

    def test_rejected_request_exposes_no_raw_body(self):
        async def handler(request):
            return httpx.Response(409, json={"detail": {"code": "dataset_not_ready"}})

        client = QlibClient(
            "http://quant-worker",
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(QuantWorkerError) as raised:
            self.run_async(client.validate_dataset("openbb-demo", "demo"))
        self.assertEqual(raised.exception.code, "dataset_not_ready")

    def test_validate_dataset_sends_universe_query(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/v1/datasets/openbb-demo/validate")
            self.assertEqual(request.url.params["universe"], "demo")
            return httpx.Response(
                200,
                json={
                    "dataset_id": "openbb-demo",
                    "universe": "demo",
                    "valid": True,
                    "limitations": LIMITATIONS,
                    "production_eligibility": "research_only",
                },
            )

        client = QlibClient(
            "http://quant-worker",
            transport=httpx.MockTransport(handler),
        )
        result = self.run_async(client.validate_dataset("openbb-demo", "demo"))
        self.assertEqual(result["universe"], "demo")


class QuantDataClientTests(unittest.TestCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_fetch_factor_snapshot_posts_to_worker_endpoint(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/v1/factors/snapshot")
            payload = json.loads(request.content)
            self.assertEqual(payload["symbols"], ["AAPL"])
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "schema_version": "1",
                    "factor_snapshot_revision_id": "fac_v1_0123456789abcdef01234567",
                    "factor_snapshot_id": "fac_v1_0123456789abcdef01234567",
                    "request_fingerprint": "rqf_v1_" + "1" * 64,
                    "content_hash": "sha256:" + "2" * 64,
                    "retrieved_at": "2026-07-08T00:00:00Z",
                    "point_in_time_status": "unsupported",
                    "provider": "yfinance",
                    "as_of_date": "2026-07-08",
                    "symbols_requested": 1,
                    "symbols_loaded": 1,
                    "factors": [{"symbol": "AAPL", "market_cap": 1000}],
                    "warnings": [],
                },
            )

        client = QuantDataClient(
            "http://quant-data-worker",
            transport=httpx.MockTransport(handler),
        )

        result = self.run_async(
            client.fetch_factor_snapshot(
                {
                    "symbols": ["AAPL"],
                    "as_of_date": "2026-07-08",
                    "provider": "yfinance",
                }
            )
        )

        self.assertEqual(
            result["factor_snapshot_revision_id"],
            "fac_v1_0123456789abcdef01234567",
        )

    def test_build_and_validate_dataset_use_canonical_manifest_contracts(self):
        revision = "dsr_v1_0123456789abcdef01234567"
        manifest_hash = "sha256:" + "2" * 64
        calls = []

        async def handler(request):
            calls.append(request.url.path)
            if request.url.path == "/v1/qlib-datasets":
                payload = json.loads(request.content)
                self.assertEqual(payload["dataset_alias"], "research-daily")
                self.assertNotIn("dataset_id", payload)
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "schema_version": "1",
                        "dataset_revision_id": revision,
                        "dataset_id": revision,
                        "dataset_alias": "research-daily",
                        "source_staging_revision_id": (
                            "stg_v1_0123456789abcdef01234567"
                        ),
                        "source_staging_id": "stg_v1_0123456789abcdef01234567",
                        "manifest_hash": manifest_hash,
                        "manifest_verified": True,
                        "limitations": LIMITATIONS,
                    },
                )
            self.assertEqual(
                request.url.path,
                f"/v1/qlib-datasets/{revision}/validate",
            )
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "schema_version": "1",
                    "dataset_revision_id": revision,
                    "dataset_id": revision,
                    "valid": True,
                    "manifest_hash": manifest_hash,
                    "manifest_verified": True,
                    "limitations": LIMITATIONS,
                },
            )

        client = QuantDataClient(
            "http://quant-data-worker",
            transport=httpx.MockTransport(handler),
        )
        build = self.run_async(
            client.build_qlib_dataset(
                {
                    "dataset_staging_id": "stg_v1_0123456789abcdef01234567",
                    "dataset_alias": "research-daily",
                    "universe_name": "custom_us_2",
                }
            )
        )
        validation = self.run_async(client.validate_qlib_dataset(revision))

        self.assertEqual(build["dataset_revision_id"], revision)
        self.assertEqual(build["dataset_alias"], "research-daily")
        self.assertTrue(validation["manifest_verified"])
        self.assertEqual(calls, ["/v1/qlib-datasets", f"/v1/qlib-datasets/{revision}/validate"])


class AiTraderClientTests(unittest.TestCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_market_overview_uses_configured_api_base(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/api/market-intel/overview")
            self.assertIn("x-request-id", request.headers)
            return httpx.Response(200, json={"available": True})

        client = AiTraderClient(
            "http://ai-trader:8100/api",
            transport=httpx.MockTransport(handler),
        )

        result = self.run_async(client.market_overview())

        self.assertTrue(result["available"])

    def test_market_news_uses_expected_endpoint_and_query(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/api/market-intel/news")
            self.assertEqual(request.url.params["category"], "macro")
            self.assertEqual(request.url.params["limit"], "3")
            self.assertEqual(request.url.params["symbols"], "AAPL,MSFT")
            return httpx.Response(200, json={"available": False, "categories": []})

        client = AiTraderClient(
            "http://ai-trader:8100/api",
            transport=httpx.MockTransport(handler),
        )

        result = self.run_async(
            client.market_news(category="macro", limit=3, symbols="AAPL,MSFT")
        )

        self.assertEqual(result["categories"], [])

    def test_signal_feed_uses_expected_endpoint_and_filters(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/api/signals/feed")
            self.assertEqual(request.url.params["market"], "us-stock")
            self.assertEqual(request.url.params["message_type"], "strategy")
            self.assertEqual(request.url.params["keyword"], "NVDA")
            self.assertEqual(request.url.params["limit"], "7")
            return httpx.Response(
                200,
                json={
                    "signals": [],
                    "total": 0,
                    "limit": 7,
                    "offset": 0,
                    "has_more": False,
                },
            )

        client = AiTraderClient(
            "http://ai-trader:8100/api",
            transport=httpx.MockTransport(handler),
        )

        result = self.run_async(
            client.signal_feed(
                market="us-stock",
                message_type="strategy",
                keyword="NVDA",
                limit=7,
            )
        )

        self.assertEqual(result["signals"], [])

    def test_publish_strategy_sends_auth_and_payload(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/api/signals/strategy")
            self.assertEqual(request.headers["authorization"], "Bearer token-1")
            self.assertEqual(request.headers["x-claw-token"], "token-1")
            payload = json.loads(request.content)
            self.assertEqual(payload["market"], "us-stock")
            return httpx.Response(200, json={"success": True, "signal_id": 7})

        client = AiTraderClient(
            "http://ai-trader:8100/api",
            token="token-1",
            transport=httpx.MockTransport(handler),
        )

        result = self.run_async(
            client.publish_strategy(
                {
                    "market": "us-stock",
                    "title": "Nexus test",
                    "content": "Research only.",
                }
            )
        )

        self.assertEqual(result["signal_id"], 7)

    def test_publish_requires_token(self):
        client = AiTraderClient("http://ai-trader:8100/api")

        with self.assertRaises(AiTraderError) as raised:
            self.run_async(client.publish_discussion({"market": "us-stock"}))

        self.assertEqual(raised.exception.code, "ai_trader_auth_required")

    def test_ai_trader_timeout_has_stable_code(self):
        async def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        client = AiTraderClient(
            "http://ai-trader:8100/api",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(AiTraderError) as raised:
            self.run_async(client.market_overview())

        self.assertEqual(raised.exception.code, "ai_trader_timeout")

    def test_ai_trader_server_error_has_stable_code(self):
        async def handler(request):
            return httpx.Response(503, json={"detail": "offline"})

        client = AiTraderClient(
            "http://ai-trader:8100/api",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(AiTraderError) as raised:
            self.run_async(client.market_overview())

        self.assertEqual(raised.exception.code, "ai_trader_unavailable")
        self.assertTrue(raised.exception.retryable)

    def test_poll_heartbeat_posts_to_auth_endpoint(self):
        async def handler(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/claw/agents/heartbeat")
            self.assertEqual(request.headers["authorization"], "Bearer token-1")
            return httpx.Response(200, json={"messages": [], "tasks": []})

        client = AiTraderClient(
            "http://ai-trader:8100/api",
            token="token-1",
            transport=httpx.MockTransport(handler),
        )

        result = self.run_async(client.poll_heartbeat())

        self.assertEqual(result["messages"], [])

    def test_ai_trader_contract_violation_has_stable_code(self):
        async def handler(request):
            return httpx.Response(200, json={"available": "yes"})

        client = AiTraderClient(
            "http://ai-trader:8100/api",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(AiTraderError) as raised:
            self.run_async(client.market_overview())

        self.assertEqual(raised.exception.code, "invalid_worker_response")
        self.assertEqual(raised.exception.stage, "contract")

    def test_ai_trader_auth_error_has_stable_code(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/api/claw/agents/heartbeat")
            return httpx.Response(401, json={"detail": "Invalid token"})

        client = AiTraderClient(
            "http://ai-trader:8100/api",
            token="bad-token",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(AiTraderError) as raised:
            self.run_async(client.poll_heartbeat())

        self.assertEqual(raised.exception.code, "ai_trader_auth_failed")


class QuantHarnessTests(unittest.TestCase):
    def test_supervisor_blocks_repeated_subagent_delegation_for_one_user_intent(self):
        middleware = DelegationLimitMiddleware(max_total=4)
        first_call = {
            "name": "task",
            "id": "task-1",
            "args": {"subagent_type": "researcher", "description": "research"},
        }
        repeated_call = {
            "name": "task",
            "id": "task-2",
            "args": {"subagent_type": "researcher", "description": "again"},
        }
        state = {
            "messages": [
                HumanMessage(content="current request"),
                AIMessage(content="", tool_calls=[first_call]),
                ToolMessage(content="done", tool_call_id="task-1"),
                AIMessage(content="", tool_calls=[repeated_call]),
            ]
        }

        result = middleware.after_model(state, SimpleNamespace())

        self.assertEqual(result["messages"][0].tool_call_id, "task-2")
        self.assertEqual(result["messages"][0].status, "error")

    def test_tool_execution_policy_reads_runtime_limits_from_settings(self):
        with patch(
            "source.tools.custom.settings",
            SimpleNamespace(
                researcher_web_search_tool_call_limit=11,
                researcher_model_call_limit=12,
                quant_researcher_experiment_tool_call_limit=13,
                quant_researcher_interpretation_tool_call_limit=14,
                quant_researcher_tool_call_limit=15,
                quant_researcher_model_call_limit=16,
                quant_data_agent_tool_call_limit=17,
                quant_data_agent_model_call_limit=18,
                ai_trader_agent_tool_call_limit=19,
                ai_trader_agent_model_call_limit=20,
            ),
        ):
            researcher_limits = ToolExecutionPolicy.researcher()
            quant_limits = ToolExecutionPolicy.quant_researcher()
            quant_data_limits = ToolExecutionPolicy.quant_data_agent()
            ai_trader_limits = ToolExecutionPolicy.ai_trader_agent()

        self.assertEqual(researcher_limits[0].run_limit, 11)
        self.assertEqual(researcher_limits[1].run_limit, 12)
        self.assertEqual(quant_limits[0].run_limit, 13)
        self.assertEqual(quant_limits[1].run_limit, 14)
        self.assertEqual(quant_limits[2].run_limit, 15)
        self.assertEqual(quant_limits[3].run_limit, 16)
        self.assertEqual(quant_data_limits[2].run_limit, 17)
        self.assertEqual(quant_data_limits[3].run_limit, 18)
        self.assertEqual(ai_trader_limits[2].run_limit, 19)
        self.assertEqual(ai_trader_limits[3].run_limit, 20)

    def test_quant_tools_are_named_and_scoped(self):
        self.assertEqual(
            [tool.name for tool in QuantTools.tools],
            [
                "list_quant_datasets",
                "run_governed_qlib_experiment",
                "get_qlib_experiment_result",
                "record_experiment_interpretation",
            ],
        )
        quant = next(agent for agent in all_subagents if agent["name"] == "quant-researcher")
        self.assertEqual(
            [tool.name for tool in quant["tools"]],
            [tool.name for tool in QuantTools.tools],
        )

    def test_quant_skill_assignment(self):
        middleware = AssignedSkillsMiddleware("quant-researcher")
        self.assertEqual(middleware.skill_names, ("quant-research",))

    def test_quant_data_skill_assignment(self):
        middleware = AssignedSkillsMiddleware("quant-data-agent")
        self.assertEqual(middleware.skill_names, ("quant-data",))

    def test_quant_data_contract_tools_are_registered(self):
        quant_data = next(
            agent for agent in all_subagents if agent["name"] == "quant-data-agent"
        )
        self.assertEqual(
            [tool.name for tool in quant_data["tools"]],
            [tool.name for tool in QuantDataTools.tools],
        )
        self.assertIn("fetch_factor_snapshot", [tool.name for tool in quant_data["tools"]])

    def test_ai_trader_contract_tools_are_registered(self):
        ai_trader = next(agent for agent in all_subagents if agent["name"] == "ai-trader-agent")
        self.assertEqual(
            [tool.name for tool in ai_trader["tools"]],
            [tool.name for tool in AiTraderTools.tools],
        )

    def test_ai_trader_skill_assignment(self):
        middleware = AssignedSkillsMiddleware("ai-trader-agent")
        self.assertEqual(middleware.skill_names, ("ai-trader",))

    def test_ai_trader_tool_returns_not_configured_without_base_url(self):
        with patch(
            "source.tools.ai_trader.settings",
            SimpleNamespace(
                ai_trader_api_base_url="",
                ai_trader_token=None,
                ai_trader_timeout_seconds=20,
            ),
        ):
            rendered = asyncio.run(
                AiTraderTools.get_ai_trader_market_overview.ainvoke({})
            )

        self.assertEqual(
            json.loads(rendered)["error"]["code"],
            "ai_trader_not_configured",
        )

    def test_ai_trader_heartbeat_uses_read_permission(self):
        with (
            patch("source.tools.ai_trader.require_permission") as require,
            patch("source.tools.ai_trader._client", return_value=None),
        ):
            asyncio.run(AiTraderTools.poll_ai_trader_heartbeat.ainvoke({}))

        require.assert_called_once_with("ai_trader:read")

    def test_ai_trader_publish_uses_runtime_approval_interrupt(self):
        middleware = next(
            item
            for item in ai_trader_agent["middleware"]
            if isinstance(item, GovernedApprovalMiddleware)
        )

        self.assertEqual(
            middleware.interrupt_on["publish_ai_trader_strategy"]["allowed_decisions"],
            ["approve", "reject"],
        )

    def test_ai_trader_publish_forwards_confirmed_strategy(self):
        class FakeClient:
            def __init__(self):
                self.request = None

            async def publish_strategy(self, request):
                self.request = request
                return {"success": True, "signal_id": 42}

        client = FakeClient()

        with patch("source.tools.ai_trader._client", return_value=client):
            rendered = asyncio.run(
                AiTraderTools.publish_ai_trader_strategy.ainvoke(
                    {
                        "market": "us-stock",
                        "title": "Nexus research",
                        "content": "Research only.",
                        "symbols": ["AAPL", "MSFT"],
                        "tags": ["qlib", "research"],
                    }
                )
            )

        payload = json.loads(rendered)
        self.assertEqual(payload["signal_id"], 42)
        self.assertEqual(client.request["symbols"], "AAPL,MSFT")
        self.assertEqual(client.request["tags"], "qlib,research")

    def test_ai_trader_market_news_tool_forwards_symbol_filter(self):
        class FakeClient:
            def __init__(self):
                self.request = None

            async def market_news(self, **kwargs):
                self.request = kwargs
                return {
                    "categories": [],
                    "filtered": True,
                    "symbols_filter": ["AAPL", "MSFT"],
                }

        client = FakeClient()

        with patch("source.tools.ai_trader._client", return_value=client):
            rendered = asyncio.run(
                AiTraderTools.get_ai_trader_market_news.ainvoke(
                    {
                        "category": "equities",
                        "limit": 10,
                        "symbols": "AAPL,MSFT",
                    }
                )
            )

        payload = json.loads(rendered)
        self.assertTrue(payload["news"]["filtered"])
        self.assertEqual(
            client.request,
            {"category": "equities", "limit": 10, "symbols": "AAPL,MSFT"},
        )

    def test_fetch_factor_snapshot_tool_forwards_request(self):
        class FakeClient:
            def __init__(self):
                self.request = None

            async def fetch_factor_snapshot(self, request):
                self.request = request
                return {
                    "ok": True,
                    "schema_version": "1",
                    "factor_snapshot_revision_id": "fac_v1_0123456789abcdef01234567",
                    "factor_snapshot_id": "fac_v1_0123456789abcdef01234567",
                    "request_fingerprint": "rqf_v1_" + "1" * 64,
                    "content_hash": "sha256:" + "2" * 64,
                    "retrieved_at": "2026-07-08T00:00:00Z",
                    "point_in_time_status": "unsupported",
                    "provider": "yfinance",
                    "as_of_date": "2026-07-08",
                    "symbols_requested": 1,
                    "symbols_loaded": 1,
                    "factors": [{"symbol": "AAPL", "market_cap": 1000}],
                    "warnings": [],
                }

        client = FakeClient()

        with (
            patch("source.tools.quant_data._client", return_value=client),
            patch("source.tools.quant_data.require_governed_effect"),
        ):
            rendered = asyncio.run(
                QuantDataTools.fetch_factor_snapshot.ainvoke(
                    {
                        "symbols": ["AAPL"],
                        "as_of_date": "2026-07-08",
                        "provider": "yfinance",
                    }
                )
            )

        self.assertEqual(
            json.loads(rendered)["factor_snapshot_revision_id"],
            "fac_v1_0123456789abcdef01234567",
        )
        self.assertEqual(client.request["symbols"], ["AAPL"])
        self.assertRegex(client.request["idempotency_key"], r"^factor:[0-9a-f]{64}$")

    def test_quant_effect_tool_fails_closed_without_approval_capability(self):
        rendered = asyncio.run(
            QuantDataTools.fetch_factor_snapshot.ainvoke(
                {
                    "symbols": ["AAPL"],
                    "as_of_date": "2026-07-08",
                    "provider": "yfinance",
                }
            )
        )

        self.assertEqual(
            json.loads(rendered)["error"]["code"],
            "governed_approval_required",
        )

    def test_quant_allows_only_one_invalid_date_correction(self):
        middleware = ToolExecutionPolicy.quant_researcher()
        submit_limit = next(
            item
            for item in middleware
            if getattr(item, "tool_name", None) == "run_governed_qlib_experiment"
        )
        self.assertEqual(
            submit_limit.run_limit,
            settings.quant_researcher_experiment_tool_call_limit,
        )

    def test_quant_research_has_headroom_without_repeating_effects(self):
        middleware = ToolExecutionPolicy.quant_researcher()
        limits = {
            getattr(item, "tool_name", "model"): item.run_limit
            for item in middleware
            if item.__class__.__name__
            in {"ToolCallLimitMiddleware", "ModelCallLimitMiddleware"}
        }

        self.assertEqual(limits[None], settings.quant_researcher_tool_call_limit)
        self.assertEqual(limits["model"], settings.quant_researcher_model_call_limit)
        self.assertGreaterEqual(limits[None], 12)
        self.assertGreaterEqual(limits["model"], 16)
        self.assertEqual(limits["run_governed_qlib_experiment"], 1)
        self.assertEqual(limits["record_experiment_interpretation"], 1)

    def test_quant_data_has_budget_for_full_pipeline(self):
        middleware = ToolExecutionPolicy.quant_data_agent()
        tool_limit = next(
            item
            for item in middleware
            if item.__class__.__name__ == "ToolCallLimitMiddleware"
            and getattr(item, "tool_name", None) is None
        )
        model_limit = next(
            item
            for item in middleware
            if item.__class__.__name__ == "ModelCallLimitMiddleware"
        )
        self.assertEqual(tool_limit.run_limit, settings.quant_data_agent_tool_call_limit)
        self.assertEqual(model_limit.run_limit, settings.quant_data_agent_model_call_limit)
