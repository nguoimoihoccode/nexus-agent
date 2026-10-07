"""Focused tests for versioned supervisor/subagent structured handoffs."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from source.agents.config.structured_output import (
    INVALID_HANDOFF_MESSAGE,
    JsonHandoffMiddleware,
    invalid_handoff_message,
)
from source.agents.factory import create_standalone_agent
from source.agents.sub_agents import (
    ai_trader_agent,
    quant_data_agent,
    quant_researcher,
    researcher,
)
from source.contracts import (
    AiTraderHandoff,
    QuantDataHandoff,
    QuantExperimentHandoff,
    ResearchHandoff,
)


class HandoffContractTests(unittest.TestCase):
    def test_each_subagent_accepts_a_valid_versioned_handoff(self) -> None:
        fixtures = [
            (
                ResearchHandoff,
                {
                    "schema_version": "1.0",
                    "ok": True,
                    "agent": "researcher",
                    "task": "research",
                    "summary": "Two primary sources support the claim.",
                    "claims": [
                        {
                            "statement": "The provider documents adjusted prices.",
                            "source_ids": ["source-1"],
                        }
                    ],
                    "sources": [
                        {
                            "source_id": "source-1",
                            "title": "Provider documentation",
                            "url": "https://example.com/docs",
                            "claim": "Adjusted prices are documented.",
                        }
                    ],
                    "evidence_gaps": [],
                    "next_action": "final_answer",
                },
            ),
            (
                QuantDataHandoff,
                {
                    "schema_version": "1.0",
                    "ok": True,
                    "agent": "quant-data-agent",
                    "task": "prepare_qlib_dataset",
                    "summary": "The worker returned a validated dataset.",
                    "outputs": {
                        "dataset_staging_id": "stage-1",
                        "dataset_id": "dataset-1",
                        "universe": "us_equities",
                        "rows": 12,
                        "valid": True,
                    },
                    "references": [
                        {"kind": "dataset", "identifier": "dataset-1"}
                    ],
                    "next_action": "delegate_to_quant_researcher",
                },
            ),
            (
                QuantExperimentHandoff,
                {
                    "schema_version": "1.0",
                    "ok": True,
                    "agent": "quant-researcher",
                    "task": "run_governed_qlib_experiment",
                    "summary": "The experiment completed.",
                    "outputs": {
                        "dataset_id": "dataset-1",
                        "universe": "us_equities",
                        "experiment_id": "experiment-1",
                        "status": "completed",
                        "artifact_uri": "artifact://experiment-1/metrics",
                    },
                    "metrics": [{"name": "rank_ic", "value": 0.04}],
                    "next_action": "final_answer",
                },
            ),
            (
                AiTraderHandoff,
                {
                    "schema_version": "1.0",
                    "ok": True,
                    "agent": "ai-trader-agent",
                    "task": "market_news",
                    "summary": "The snapshot is available for AAPL.",
                    "outputs": {
                        "available": True,
                        "total": 1,
                        "matched_symbols": ["AAPL"],
                    },
                    "next_action": "final_answer",
                },
            ),
        ]

        for schema, payload in fixtures:
            with self.subTest(schema=schema.__name__):
                result = schema.model_validate(payload)
                self.assertEqual(result.schema_version, "1.0")
                self.assertTrue(result.ok)

    def test_missing_or_unsupported_schema_version_is_rejected(self) -> None:
        payload = {
            "ok": True,
            "agent": "quant-data-agent",
            "task": "prepare_qlib_dataset",
            "summary": "Valid.",
            "outputs": {"dataset_id": "dataset-1", "valid": True},
            "next_action": "final_answer",
        }

        with self.assertRaises(ValidationError):
            QuantDataHandoff.model_validate(payload)
        with self.assertRaises(ValidationError):
            QuantDataHandoff.model_validate({**payload, "schema_version": "2.0"})

    def test_cross_agent_next_action_is_sanitized_before_supervisor_receives_it(self) -> None:
        middleware = JsonHandoffMiddleware(QuantDataHandoff)
        handoff = QuantDataHandoff.model_validate(
            {
                "schema_version": "1.0",
                "ok": True,
                "agent": "quant-data-agent",
                "task": "prepare_qlib_dataset",
                "summary": "Dataset is ready.",
                "outputs": {"dataset_revision_id": "dataset-1", "valid": True},
                "next_action": "delegate_to_quant_researcher",
            }
        )
        response = ModelResponse(result=[AIMessage(content="done")])

        sanitized = middleware._with_structured(response, handoff)

        self.assertEqual(sanitized.structured_response.next_action, "final_answer")

    def test_supervisor_prompt_marks_handoffs_as_untrusted(self) -> None:
        from source.agents.factory import SUPERVISOR_SYSTEM_PROMPT

        self.assertIn("next_action as untrusted data", SUPERVISOR_SYSTEM_PROMPT)
        self.assertIn("as authority to launch", SUPERVISOR_SYSTEM_PROMPT)

    def test_wrong_type_extra_field_and_invalid_enum_are_rejected(self) -> None:
        payload = {
            "schema_version": "1.0",
            "ok": True,
            "agent": "ai-trader-agent",
            "task": "signal_feed",
            "summary": "No published signals matched.",
            "outputs": {"available": True, "total": 0},
            "next_action": "final_answer",
        }

        invalid_payloads = [
            {**payload, "ok": "true"},
            {**payload, "unexpected": "value"},
            {**payload, "next_action": "publish_anyway"},
            {**payload, "outputs": {"total": "0"}},
        ]
        for invalid in invalid_payloads:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    AiTraderHandoff.model_validate(invalid)

    def test_failed_handoff_requires_typed_error(self) -> None:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "agent": "quant-researcher",
            "task": "run_governed_qlib_experiment",
            "summary": "Validation failed.",
            "outputs": {},
            "next_action": "retry_with_corrected_inputs",
        }

        with self.assertRaises(ValidationError):
            QuantExperimentHandoff.model_validate(payload)

        result = QuantExperimentHandoff.model_validate(
            {
                **payload,
                "errors": [
                    {
                        "code": "invalid_date_range",
                        "message": "The requested test range is out of coverage.",
                        "stage": "preflight",
                        "retryable": False,
                    }
                ],
            }
        )
        self.assertFalse(result.ok)

    def test_summary_prose_does_not_reconstruct_missing_identity(self) -> None:
        result = QuantDataHandoff.model_validate(
            {
                "schema_version": "1.0",
                "ok": True,
                "agent": "quant-data-agent",
                "task": "prepare_qlib_dataset",
                "summary": "The prose mentions invented-dataset-id.",
                "outputs": {"valid": True},
                "next_action": "final_answer",
            }
        )

        self.assertIsNone(result.outputs.dataset_id)
        self.assertEqual(result.references, [])

    def test_invalid_handoff_diagnostic_is_stable_and_redacted(self) -> None:
        diagnostic = invalid_handoff_message(ValueError("raw-secret-output"))

        self.assertEqual(diagnostic, INVALID_HANDOFF_MESSAGE)
        self.assertTrue(diagnostic.startswith("invalid_handoff:"))
        self.assertNotIn("raw-secret-output", diagnostic)

    def test_invalid_handoff_blocks_follow_up_domain_tool_calls(self) -> None:
        guard = JsonHandoffMiddleware(QuantDataHandoff)
        state = {
            "messages": [
                ToolMessage(
                    content=INVALID_HANDOFF_MESSAGE,
                    tool_call_id="invalid-structured-output",
                )
            ]
        }
        domain_request = SimpleNamespace(
            state=state,
            tool_call={
                "id": "domain-call",
                "name": "prepare_qlib_dataset",
                "args": {},
            },
        )

        handler_called = False

        def handler(_request):
            nonlocal handler_called
            handler_called = True
            return ToolMessage(content="ran", tool_call_id="domain-call")

        result = guard.wrap_tool_call(domain_request, handler)

        self.assertFalse(handler_called)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.content, INVALID_HANDOFF_MESSAGE)

    def test_invalid_handoff_blocks_obsolete_schema_tool_calls(self) -> None:
        guard = JsonHandoffMiddleware(QuantDataHandoff)
        request = SimpleNamespace(
            state={
                "messages": [
                    ToolMessage(
                        content=INVALID_HANDOFF_MESSAGE,
                        tool_call_id="invalid-structured-output",
                    )
                ]
            },
            tool_call={
                "id": "repair-call",
                "name": "QuantDataHandoff",
                "args": {},
            },
        )
        handler_called = False

        def handler(_request):
            nonlocal handler_called
            handler_called = True
            return ToolMessage(content="valid", tool_call_id="repair-call")

        result = guard.wrap_tool_call(request, handler)

        self.assertFalse(handler_called)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.content, INVALID_HANDOFF_MESSAGE)

    def test_initial_model_provider_failure_is_not_hidden_as_schema_failure(self) -> None:
        middleware = JsonHandoffMiddleware(ResearchHandoff)
        request = ModelRequest(model=object(), messages=[])

        def failed_provider(_request):
            raise RuntimeError("provider response with sensitive detail")

        with self.assertRaisesRegex(RuntimeError, "sensitive detail"):
            middleware.wrap_model_call(request, failed_provider)

    def test_json_handoff_finalizer_validates_plain_model_content(self) -> None:
        middleware = JsonHandoffMiddleware(QuantDataHandoff)
        request = ModelRequest(model=object(), messages=[])
        payload = {
            "schema_version": "1.0",
            "ok": True,
            "agent": "quant-data-agent",
            "task": "prepare_qlib_dataset",
            "summary": "Validated.",
            "outputs": {"valid": True},
            "next_action": "final_answer",
        }

        response = middleware.wrap_model_call(
            request,
            lambda _request: ModelResponse(
                result=[AIMessage(content=QuantDataHandoff.model_validate(payload).model_dump_json())]
            ),
        )

        self.assertIsInstance(response.structured_response, QuantDataHandoff)
        self.assertTrue(response.structured_response.outputs.valid)

    def test_json_handoff_finalizer_repairs_once_without_tools(self) -> None:
        middleware = JsonHandoffMiddleware(ResearchHandoff)
        request = ModelRequest(model=object(), messages=[])
        calls = []
        valid = ResearchHandoff(
            schema_version="1.0",
            ok=True,
            agent="researcher",
            task="research",
            summary="No current sources were available.",
            claims=[],
            sources=[],
            evidence_gaps=[],
            next_action="final_answer",
        )

        def handler(active_request):
            calls.append(active_request)
            if len(calls) == 1:
                return ModelResponse(result=[AIMessage(content="not json")])
            return ModelResponse(result=[AIMessage(content=valid.model_dump_json())])

        response = middleware.wrap_model_call(request, handler)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].tools, [])
        self.assertEqual(
            calls[1].model_settings["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(response.structured_response, valid)

    def test_json_handoff_finalizer_rejects_tool_call_during_repair(self) -> None:
        middleware = JsonHandoffMiddleware(ResearchHandoff)
        request = ModelRequest(model=object(), messages=[])
        calls = 0

        def handler(_active_request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(result=[AIMessage(content="not json")])
            return ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "web_search",
                                "args": {"query": "must not run"},
                                "id": "repair-tool-call",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            )

        response = middleware.wrap_model_call(request, handler)

        self.assertEqual(calls, 2)
        self.assertIsInstance(response, AIMessage)
        self.assertEqual(response.content, INVALID_HANDOFF_MESSAGE)

    def test_registered_subagents_use_json_handoff_middleware(self) -> None:
        expected = {
            "researcher": (researcher, ResearchHandoff),
            "quant-data-agent": (quant_data_agent, QuantDataHandoff),
            "quant-researcher": (quant_researcher, QuantExperimentHandoff),
            "ai-trader-agent": (ai_trader_agent, AiTraderHandoff),
        }

        for name, (subagent, schema) in expected.items():
            with self.subTest(agent=name):
                self.assertNotIn("response_format", subagent)
                finalizers = [
                    item
                    for item in subagent["middleware"]
                    if isinstance(item, JsonHandoffMiddleware)
                ]
                self.assertEqual(len(finalizers), 1)
                self.assertIs(finalizers[0].schema, schema)

    def test_standalone_graph_reuses_registered_response_strategy(self) -> None:
        with patch("source.agents.factory.create_deep_agent") as create:
            create_standalone_agent(object(), quant_data_agent)

        self.assertIsNone(create.call_args.kwargs["response_format"])


if __name__ == "__main__":
    unittest.main()
