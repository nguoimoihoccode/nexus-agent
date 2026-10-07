"""Focused tests for researcher skill and tool wiring."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse

from source.agents.config.skills import MAX_SKILL_BYTES
from source.agents.config import (
    AssignedSkillsMiddleware,
    ForbiddenToolMiddleware,
    NEXUS_EXCLUDED_TOOLS,
    nexus_harness_profile,
)
from source.agents.config.harness import register_nexus_harness_profile
from source.agents.approval_middleware import GovernedApprovalMiddleware
from source.agents.governed_effects import GovernedEffectMiddleware, QUANT_EFFECT_TOOLS
from source.agents.sub_agents import (
    ai_trader_agent,
    quant_data_agent,
    quant_researcher,
    researcher,
)
from source.tools.ai_trader import AiTraderTools
from source.tools.custom import CustomTools, format_web_search_results
from source.tools.quant_data import QuantDataTools
from source.tools.quant import QuantTools
from source.security import PermissionDenied


class ResearchWiringTests(unittest.TestCase):
    def test_nexus_harness_profile_disables_general_purpose_and_execute(self) -> None:
        profile = nexus_harness_profile()

        self.assertEqual(profile.excluded_tools, NEXUS_EXCLUDED_TOOLS)
        self.assertIn("execute", profile.excluded_tools)
        self.assertTrue(
            any(
                isinstance(middleware, ForbiddenToolMiddleware)
                for middleware in profile.extra_middleware()
            )
        )
        self.assertIsNotNone(profile.general_purpose_subagent)
        self.assertIs(profile.general_purpose_subagent.enabled, False)

    def test_forged_filesystem_tool_call_is_blocked_at_execution(self) -> None:
        middleware = ForbiddenToolMiddleware(NEXUS_EXCLUDED_TOOLS)
        request = SimpleNamespace(
            tool_call={"name": "read_file", "id": "forged-read", "args": {}}
        )

        result = middleware.wrap_tool_call(
            request,
            lambda _request: self.fail("forbidden handler must not execute"),
        )

        self.assertEqual(result.status, "error")
        self.assertIn("disabled", result.content)

    def test_every_graph_has_exact_model_visible_tool_allowlist(self) -> None:
        from source.agents.agent import _runtime

        expected = {
            "supervisor": {
                "delete_user_memory",
                "save_user_memory",
                "task",
                "write_todos",
            },
            "researcher": {"web_search", "write_todos"},
            "quant-data-agent": {
                "fetch_factor_snapshot",
                "prepare_qlib_dataset",
                "write_todos",
            },
            "quant-researcher": {
                "get_qlib_experiment_result",
                "list_quant_datasets",
                "record_experiment_interpretation",
                "run_governed_qlib_experiment",
                "write_todos",
            },
            "ai-trader-agent": {
                "get_ai_trader_market_news",
                "get_ai_trader_market_overview",
                "get_ai_trader_signal_feed",
                "poll_ai_trader_heartbeat",
                "publish_ai_trader_discussion",
                "publish_ai_trader_strategy",
                "write_todos",
            },
        }
        exclusion = _ToolExclusionMiddleware(excluded=NEXUS_EXCLUDED_TOOLS)

        for graph_name, graph in _runtime.agents.items():
            tool_node = graph.get_graph().nodes["tools"].data
            request = ModelRequest(
                model=SimpleNamespace(),
                messages=[],
                tools=list(tool_node.tools_by_name.values()),
            )
            visible: set[str] = set()

            def capture(filtered_request):
                visible.update(tool.name for tool in filtered_request.tools)
                return ModelResponse(result=[])

            exclusion.wrap_model_call(request, capture)

            self.assertEqual(visible, expected[graph_name])
            self.assertTrue(visible.isdisjoint(NEXUS_EXCLUDED_TOOLS))

    def test_nexus_harness_registration_is_idempotent(self) -> None:
        with patch("source.agents.config.harness.register_harness_profile") as register:
            register_nexus_harness_profile("unit-test-provider:unit-test-model")
            register_nexus_harness_profile("unit-test-provider:unit-test-model")

        self.assertEqual(
            [call.args[0] for call in register.call_args_list],
            ["unit-test-provider", "unit-test-provider:unit-test-model"],
        )

    def test_researcher_receives_only_full_assigned_skill_content(self) -> None:
        middleware = AssignedSkillsMiddleware("researcher")

        self.assertEqual(middleware.skill_names, ("research",))
        self.assertIn("name: research", middleware.system_prompt)
        self.assertNotIn("name: quant-research", middleware.system_prompt)
        self.assertIn("do not try to read them from the filesystem", middleware.system_prompt)

    def test_trusted_skill_loader_rejects_oversized_and_escaping_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".deepagents"
            skill = root / "skills" / "oversized" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_bytes(
                b"---\nname: oversized\ndescription: test\n---\n"
                + b"x" * MAX_SKILL_BYTES
            )
            with (
                patch("source.agents.config.skills.DEEPAGENTS_DIR", root),
                patch(
                    "source.agents.config.skills.skill_names_for_agent",
                    return_value=["oversized"],
                ),
            ):
                with self.assertRaisesRegex(ValueError, "exceeds"):
                    AssignedSkillsMiddleware("researcher")

            outside = Path(temporary) / "escape" / "SKILL.md"
            outside.parent.mkdir(parents=True)
            outside.write_text(
                "---\nname: escape\ndescription: test\n---\nbody",
                encoding="utf-8",
            )
            (root / "skills").mkdir(parents=True, exist_ok=True)
            (root / "skills" / "escape").symlink_to(outside.parent)
            with (
                patch("source.agents.config.skills.DEEPAGENTS_DIR", root),
                patch(
                    "source.agents.config.skills.skill_names_for_agent",
                    return_value=["escape"],
                ),
            ):
                with self.assertRaisesRegex(ValueError, "escapes"):
                    AssignedSkillsMiddleware("researcher")

    def test_search_results_keep_titles_urls_and_extracts(self) -> None:
        formatted = format_web_search_results(
            {
                "results": [
                    {
                        "title": "Primary source",
                        "url": "https://example.com/source",
                        "content": "Evidence.",
                    }
                ]
            }
        )

        self.assertIn("Primary source", formatted)
        self.assertIn("https://example.com/source", formatted)
        self.assertIn("Evidence.", formatted)

    def test_web_search_checks_research_permission_before_outbound_work(self) -> None:
        with patch(
            "source.tools.custom.require_permission",
            side_effect=PermissionDenied("research:read"),
        ):
            rendered = asyncio.run(CustomTools.researcher[0].ainvoke({"query": "x"}))

        self.assertEqual(json.loads(rendered)["error"]["code"], "forbidden")

    def test_tool_classes_expose_tools_by_runtime_role(self) -> None:
        self.assertEqual(
            [tool.name for tool in CustomTools.researcher],
            ["web_search"],
        )

    def test_model_facing_effect_tools_do_not_expose_retry_identity_controls(self) -> None:
        effect_tools = [
            QuantDataTools.prepare_qlib_dataset,
            QuantDataTools.fetch_factor_snapshot,
            QuantTools.run_governed_qlib_experiment,
            QuantTools.record_experiment_interpretation,
            AiTraderTools.publish_ai_trader_strategy,
            AiTraderTools.publish_ai_trader_discussion,
        ]

        for runtime_tool in effect_tools:
            with self.subTest(tool=runtime_tool.name):
                properties = runtime_tool.args_schema.model_json_schema()["properties"]
                self.assertNotIn("force_new", properties)
                self.assertNotIn("idempotency_key", properties)
        self.assertEqual(
            [tool.name for tool in QuantTools.tools],
            [
                "list_quant_datasets",
                "run_governed_qlib_experiment",
                "get_qlib_experiment_result",
                "record_experiment_interpretation",
            ],
        )
        self.assertEqual(
            [tool.name for tool in QuantDataTools.tools],
            [
                "prepare_qlib_dataset",
                "fetch_factor_snapshot",
            ],
        )
        self.assertEqual(
            [tool.name for tool in AiTraderTools.tools],
            [
                "get_ai_trader_market_overview",
                "get_ai_trader_market_news",
                "get_ai_trader_signal_feed",
                "publish_ai_trader_strategy",
                "publish_ai_trader_discussion",
                "poll_ai_trader_heartbeat",
            ],
        )

    def test_researcher_runtime_scope_is_read_only_and_research_only(self) -> None:
        self.assertEqual(researcher["name"], "researcher")
        self.assertEqual(
            [tool.name for tool in researcher["tools"]],
            ["web_search"],
        )

        self.assertNotIn("permissions", researcher)

    def test_researcher_prompt_declares_quant_boundaries(self) -> None:
        prompt = researcher["system_prompt"]

        self.assertIn("Do not call OpenBB, Qlib, quant-worker", prompt)
        self.assertIn("Do not fetch, create, normalize, modify, validate", prompt)
        self.assertIn("Do not run, poll, or interpret Qlib experiments", prompt)
        self.assertIn("Return citations", prompt)

    def test_quant_data_agent_is_scoped_to_contract_tools(self) -> None:
        self.assertEqual(quant_data_agent["name"], "quant-data-agent")
        self.assertEqual(
            [tool.name for tool in quant_data_agent["tools"]],
            [
                "prepare_qlib_dataset",
                "fetch_factor_snapshot",
            ],
        )

        self.assertNotIn("permissions", quant_data_agent)

        approval = next(
            item
            for item in quant_data_agent["middleware"]
            if isinstance(item, GovernedApprovalMiddleware)
        )
        self.assertEqual(
            set(approval.interrupt_on),
            {"prepare_qlib_dataset", "fetch_factor_snapshot"},
        )
        self.assertTrue(
            any(
                isinstance(item, GovernedEffectMiddleware)
                for item in quant_data_agent["middleware"]
            )
        )

    def test_quant_data_prompt_declares_mvp_boundaries(self) -> None:
        prompt = " ".join(quant_data_agent["system_prompt"].split())

        self.assertIn("never invent staged datasets", prompt)
        self.assertIn("Do not run Qlib experiments", prompt)
        self.assertIn("build Qlib dataset", prompt)

    def test_quant_researcher_is_scoped_to_contract_tools(self) -> None:
        self.assertEqual(quant_researcher["name"], "quant-researcher")
        self.assertEqual(
            [tool.name for tool in quant_researcher["tools"]],
            [tool.name for tool in QuantTools.tools],
        )

        self.assertNotIn("permissions", quant_researcher)

        approval = next(
            item
            for item in quant_researcher["middleware"]
            if isinstance(item, GovernedApprovalMiddleware)
        )
        self.assertEqual(
            set(approval.interrupt_on),
            {
                "run_governed_qlib_experiment",
                "record_experiment_interpretation",
            },
        )
        self.assertEqual(
            set(approval.interrupt_on) | set(
                next(
                    item
                    for item in quant_data_agent["middleware"]
                    if isinstance(item, GovernedApprovalMiddleware)
                ).interrupt_on
            ),
            QUANT_EFFECT_TOOLS,
        )

    def test_ai_trader_agent_is_scoped_to_contract_tools(self) -> None:
        self.assertEqual(ai_trader_agent["name"], "ai-trader-agent")
        self.assertEqual(
            [tool.name for tool in ai_trader_agent["tools"]],
            [tool.name for tool in AiTraderTools.tools],
        )

        self.assertNotIn("permissions", ai_trader_agent)

    def test_ai_trader_prompt_declares_publish_boundaries(self) -> None:
        prompt = " ".join(ai_trader_agent["system_prompt"].split())

        self.assertIn("runtime approval interrupt", prompt)
        self.assertIn("authenticated approve decision", prompt)
        self.assertIn("Do not submit realtime trades", prompt)
        self.assertIn("AI_TRADER_API_BASE_URL", prompt)
        self.assertIn("Do not describe that state as an", prompt)
        self.assertIn("AI_TRADER_API_BASE_URL", prompt)
        self.assertIn("configuration problem", prompt)
        self.assertIn("no cached snapshot or published signal data", prompt)
        self.assertIn("`symbols` filter", prompt)
        self.assertIn("do not claim the entire snapshot lacks a ticker", prompt)


if __name__ == "__main__":
    unittest.main()
