"""Worker-backed OpenBB-to-Qlib data tools for the quant data agent."""

import json
from datetime import date
from typing import Any, Literal

from langchain_core.tools import tool

from source.core.config import settings
from source.core.observability import correlation_ids
from source.application import PrepareQlibDatasetWorkflow
from source.contracts import PrepareQlibDatasetIntent
from source.contracts.identity import action_fingerprint
from source.infrastructure.runtime_resources import require_domain_pool
from source.infrastructure.workflow_repository import PostgresWorkflowRepository
from source.integrations.quant_data_client import (
    QuantDataClient,
    QuantDataWorkerError,
)
from source.security import PermissionDenied, require_permission
from source.agents.governed_effects import (
    GovernedEffectDenied,
    require_governed_effect,
)


def _client() -> QuantDataClient:
    return QuantDataClient(
        settings.quant_data_worker_url,
        timeout_seconds=settings.quant_worker_timeout_seconds,
    )


def _prepare_workflow() -> PrepareQlibDatasetWorkflow:
    return PrepareQlibDatasetWorkflow(
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


class QuantDataTools:
    """OpenBB/Qlib dataset tools backed by the internal quant data worker."""

    @staticmethod
    @tool(parse_docstring=True)
    async def prepare_qlib_dataset(
        symbols: list[str],
        start_date: date,
        end_date: date,
        dataset_alias: str,
        universe_name: str,
        provider: Literal["yfinance"] = "yfinance",
        frequency: Literal["1d"] = "1d",
        adjustment: Literal[
            "auto", "adjusted", "unadjusted", "provider_default"
        ] = "auto",
        include_fields: list[str] | None = None,
    ) -> str:
        """Prepare one validated immutable Qlib dataset through fixed transitions.

        Args:
            symbols: Unique symbols normalized by trusted workflow code.
            start_date: Inclusive first market-data date.
            end_date: Inclusive last market-data date.
            dataset_alias: Human-friendly alias, never the canonical revision ID.
            universe_name: Instruments universe name inside the Qlib revision.
            provider: Allowlisted OpenBB provider.
            frequency: Daily frequency only.
            adjustment: Explicit requested price adjustment policy.
            include_fields: Optional allowlisted Qlib fields; close/factor are required.
        """
        if error := _authorize(
            "quant:data:write",
            effect_tool="prepare_qlib_dataset",
        ):
            return error
        payload: dict[str, Any] = {
            "symbols": symbols,
            "start_date": start_date,
            "end_date": end_date,
            "dataset_alias": dataset_alias,
            "universe_name": universe_name,
            "provider": provider,
            "frequency": frequency,
            "adjustment": adjustment,
        }
        if include_fields is not None:
            payload["include_fields"] = include_fields
        intent = PrepareQlibDatasetIntent.model_validate(payload)
        from source.security import current_actor_key
        thread_id, run_id = correlation_ids()

        return _render(
            await _prepare_workflow().execute(
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
    async def fetch_factor_snapshot(
        symbols: list[str],
        as_of_date: date,
        provider: Literal["yfinance"],
    ) -> str:
        """Fetch current fundamental/factor snapshot context through OpenBB.

        Args:
            symbols: User-provided symbols to fetch.
            as_of_date: Metadata date for the snapshot; not point-in-time history.
            provider: Allowlisted OpenBB provider. MVP accepts only "yfinance".
        """
        if error := _authorize(
            "quant:data:write",
            effect_tool="fetch_factor_snapshot",
        ):
            return error
        request = {
            "symbols": symbols,
            "as_of_date": as_of_date,
            "provider": provider,
        }
        from source.security import current_actor_key

        normalized = json.loads(json.dumps(request, default=str))
        digest = action_fingerprint(
            {
                "actor_key": current_actor_key(),
                "arguments": normalized,
                "operation": "provider.factor_snapshot",
                "schema_version": "1",
                "target_boundary": "quant-data-worker",
            }
        )
        idempotency_key = f"factor:{digest[7:]}"
        normalized["idempotency_key"] = idempotency_key
        try:
            return _render(
                await _client().fetch_factor_snapshot(normalized)
            )
        except QuantDataWorkerError as exc:
            return _render(exc.as_dict())

    tools: list[Any]


QuantDataTools.tools = [
    QuantDataTools.prepare_qlib_dataset,
    QuantDataTools.fetch_factor_snapshot,
]
