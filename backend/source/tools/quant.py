"""Allowlisted Qlib tools exposed only to the quant researcher."""

import asyncio
import json
from datetime import date
from typing import Any, Literal

from langchain_core.tools import tool

from source.application import RunQlibExperimentWorkflow
from source.contracts import RunQlibExperimentIntent
from source.core.config import settings
from source.core.observability import correlation_ids
from source.infrastructure.runtime_resources import require_domain_pool
from source.infrastructure.interpretation_repository import (
    InterpretationConflict,
    PostgresInterpretationRepository,
)
from source.infrastructure.workflow_repository import PostgresWorkflowRepository
from source.integrations.qlib_client import QlibClient, QuantWorkerError
from source.security import PermissionDenied, require_permission
from source.agents.governed_effects import (
    GovernedEffectDenied,
    require_governed_effect,
)


def _client() -> QlibClient:
    return QlibClient(
        settings.quant_worker_url,
        timeout_seconds=settings.quant_worker_timeout_seconds,
    )


def _experiment_workflow() -> RunQlibExperimentWorkflow:
    return RunQlibExperimentWorkflow(
        PostgresWorkflowRepository(require_domain_pool()),
        _client(),
    )


def _render(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _authorize(permission: str, *, effect_tool: str | None = None) -> str | None:
    try:
        require_permission(permission)
        if effect_tool is not None:
            require_governed_effect(effect_tool)
    except PermissionDenied as exc:
        return _render(
            {"ok": False, "error": {"code": "forbidden", "message": str(exc)}}
        )
    except GovernedEffectDenied as exc:
        return _render(
            {
                "ok": False,
                "error": {"code": str(exc), "message": "Explicit approval is required."},
            }
        )
    return None


class QuantTools:
    """Deterministic tools backed by the internal Qlib worker."""

    @staticmethod
    @tool(parse_docstring=True)
    async def run_governed_qlib_experiment(
        train_start: date,
        train_end: date,
        valid_start: date,
        valid_end: date,
        test_start: date,
        test_end: date,
        dataset_revision_id: str,
        universe: str,
        model: Literal["lightgbm", "linear", "xgboost", "catboost"] = "lightgbm",
        topk: int = 50,
        n_drop: int = 5,
        account: float = 100_000_000,
        open_cost: float = 0.0005,
        close_cost: float = 0.0015,
        min_cost: float = 5,
    ) -> str:
        """Validate and submit one durable Qlib job through fixed transitions.

        Args:
            train_start: First training date.
            train_end: Last training date.
            valid_start: First validation date.
            valid_end: Last validation date.
            test_start: First out-of-sample date.
            test_end: Last out-of-sample date.
            dataset_revision_id: Canonical manifest-verified dataset revision.
            universe: Universe inside the selected revision.
            model: Allowlisted Qlib model preset.
            topk: Highest-scored holdings retained.
            n_drop: Maximum holdings replaced per rebalance.
            account: Initial simulated cash.
            open_cost: Fractional buy cost.
            close_cost: Fractional sell cost.
            min_cost: Minimum transaction cost.
        """
        if error := _authorize(
            "quant:experiment:run",
            effect_tool="run_governed_qlib_experiment",
        ):
            return error
        intent = RunQlibExperimentIntent.model_validate(
            {
                "dataset_revision_id": dataset_revision_id,
                "universe": universe,
                "model": model,
                "train": {"start": train_start, "end": train_end},
                "valid": {"start": valid_start, "end": valid_end},
                "test": {"start": test_start, "end": test_end},
                "topk": topk,
                "n_drop": n_drop,
                "account": account,
                "open_cost": open_cost,
                "close_cost": close_cost,
                "min_cost": min_cost,
            }
        )
        from source.security import current_actor_key
        thread_id, run_id = correlation_ids()

        return _render(
            await _experiment_workflow().execute(
                actor_key=current_actor_key(),
                intent=intent,
                idempotency_key=None,
                force_new=False,
                thread_id=thread_id,
                run_id=run_id,
            )
        )

    @staticmethod
    @tool(parse_docstring=True)
    async def list_quant_datasets() -> str:
        """List allowlisted Qlib datasets, readiness and calendar coverage."""
        if error := _authorize("quant:read"):
            return error
        try:
            return _render({"ok": True, "datasets": await _client().list_datasets()})
        except QuantWorkerError as exc:
            return _render(exc.as_dict())

    @staticmethod
    @tool(parse_docstring=True)
    async def get_qlib_experiment_result(
        experiment_id: str, wait_seconds: int = 0
    ) -> str:
        """Get an experiment result, optionally polling for a bounded time.

        Args:
            experiment_id: Identifier returned by the governed experiment workflow.
            wait_seconds: Seconds to poll before returning pending, from 0 to 30.
        """
        if error := _authorize("quant:read"):
            return error
        wait_seconds = max(0, min(wait_seconds, int(settings.quant_worker_wait_seconds)))
        deadline = asyncio.get_running_loop().time() + wait_seconds
        try:
            while True:
                result = await _client().get_result(experiment_id)
                if result.get("status") in {"completed", "failed"}:
                    return _render({"ok": True, **result})
                if asyncio.get_running_loop().time() >= deadline:
                    return _render({"ok": True, **result})
                await asyncio.sleep(settings.quant_worker_poll_seconds)
        except QuantWorkerError as exc:
            return _render(exc.as_dict())

    @staticmethod
    @tool(parse_docstring=True)
    async def record_experiment_interpretation(
        experiment_id: str,
        summary: str,
        findings: list[dict[str, str]],
        evidence_ids: list[str],
    ) -> str:
        """Persist a bounded model-generated interpretation linked to exact evidence.

        Args:
            experiment_id: Completed experiment being interpreted.
            summary: Bounded research-only interpretation, never investment advice.
            findings: Metric-specific items with metric_id and statement keys.
            evidence_ids: Existing actor-owned metric, artifact, dataset, or source IDs.
        """
        if error := _authorize(
            "quant:interpretation:write",
            effect_tool="record_experiment_interpretation",
        ):
            return error
        if not (1 <= len(summary) <= 2000) or len(findings) > 50:
            return _render(
                {"ok": False, "error": {"code": "invalid_interpretation"}}
            )
        normalized_findings: list[dict[str, str]] = []
        for item in findings:
            metric_id = str(item.get("metric_id") or "")
            statement = str(item.get("statement") or "")
            if (
                not metric_id.startswith("lin_v1_")
                or len(metric_id) > 200
                or not 1 <= len(statement) <= 1200
            ):
                return _render(
                    {"ok": False, "error": {"code": "invalid_interpretation"}}
                )
            normalized_findings.append(
                {"metric_id": metric_id, "statement": statement}
            )
        references = sorted(set(str(value) for value in evidence_ids))
        if len(references) > 100 or any(
            not value
            or len(value) > 200
            or not all(character.isalnum() or character in "._:-" for character in value)
            for value in references
        ):
            return _render(
                {"ok": False, "error": {"code": "invalid_evidence_reference"}}
            )
        content = {
            "summary": summary,
            "findings": normalized_findings,
            "disclaimer": "Historical research only; not investment advice.",
        }
        from source.contracts.identity import content_hash
        from source.security import current_actor_key

        digest = content_hash(
            "experiment_interpretation_tool",
            {
                "experiment_id": experiment_id,
                "content": content,
                "evidence_ids": references,
            },
        )
        thread_id, run_id = correlation_ids()
        try:
            report = await PostgresInterpretationRepository(
                require_domain_pool()
            ).record(
                actor_key=current_actor_key(),
                experiment_id=experiment_id,
                structured_content=content,
                evidence_references=references,
                idempotency_key=f"interpretation:{digest[7:]}",
                thread_id=thread_id,
                run_id=run_id,
            )
        except InterpretationConflict as exc:
            return _render(
                {"ok": False, "error": {"code": str(exc), "retryable": False}}
            )
        return _render({"ok": True, **report})

    tools: list[Any]


QuantTools.tools = [
    QuantTools.list_quant_datasets,
    QuantTools.run_governed_qlib_experiment,
    QuantTools.get_qlib_experiment_result,
    QuantTools.record_experiment_interpretation,
]
