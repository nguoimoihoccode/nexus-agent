"""Approval-backed execution capability for model-triggered domain effects."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from source.core.config import settings
from source.domain import (
    ApprovalConflict,
    approval_action_identity,
    normalize_governed_arguments,
)
from source.infrastructure.approval_repository import PostgresApprovalRepository
from source.infrastructure.runtime_resources import optional_domain_pool
from source.security import current_actor_key

QUANT_EFFECT_TOOLS = frozenset(
    {
        "prepare_qlib_dataset",
        "fetch_factor_snapshot",
        "run_governed_qlib_experiment",
        "record_experiment_interpretation",
    }
)

_effect_capability: ContextVar[tuple[str, str] | None] = ContextVar(
    "quant_effect_capability",
    default=None,
)


class GovernedEffectDenied(RuntimeError):
    """Raised when a side-effect tool runs without its approved capability."""


def require_governed_effect(tool_name: str) -> None:
    capability = _effect_capability.get()
    if capability is None or capability[0] != tool_name:
        raise GovernedEffectDenied("governed_approval_required")


@contextmanager
def governed_effect_capability(tool_name: str, action_digest: str) -> Iterator[None]:
    """Grant one exact effect capability for the duration of a trusted call."""
    token = _effect_capability.set((tool_name, action_digest))
    try:
        yield
    finally:
        _effect_capability.reset(token)


def _safe_execution_result(result: Any, tool_name: str) -> tuple[bool, dict[str, Any]]:
    content = result.content if isinstance(result, ToolMessage) else result
    if not isinstance(content, str):
        return False, {"code": "invalid_tool_result", "tool_name": tool_name}
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return False, {"code": "invalid_tool_result", "tool_name": tool_name}
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        error = payload.get("error") if isinstance(payload, dict) else None
        return False, {
            "code": str(
                error.get("code") if isinstance(error, dict) else "effect_failed"
            )[:96],
            "tool_name": tool_name,
        }
    reference_keys = {
        "dataset_revision_id",
        "factor_snapshot_revision_id",
        "experiment_id",
        "interpretation_report_id",
        "request_fingerprint",
        "content_hash",
        "status",
    }
    return True, {
        "effect_type": tool_name,
        **{key: payload[key] for key in reference_keys if key in payload},
    }


class GovernedEffectMiddleware(AgentMiddleware):
    """Verify durable approval before granting one exact quant tool execution."""

    async def awrap_tool_call(self, request, handler):
        tool_name = str(request.tool_call.get("name") or "")
        if tool_name not in QUANT_EFFECT_TOOLS:
            return await handler(request)
        pool = optional_domain_pool()
        if pool is None or not hasattr(pool, "connection"):
            raise GovernedEffectDenied("governed_approval_control_unavailable")
        arguments = request.tool_call.get("args") or {}
        if not isinstance(arguments, dict):
            raise GovernedEffectDenied("governed_arguments_invalid")
        tool_call_id = str(request.tool_call.get("id") or "")
        if not tool_call_id:
            raise GovernedEffectDenied("governed_tool_call_id_missing")

        actor_key = current_actor_key()
        normalized = normalize_governed_arguments(
            tool_name,
            arguments,
            tool_call_id=tool_call_id,
        )
        target, digest = approval_action_identity(
            actor_key=actor_key,
            tool_name=tool_name,
            normalized_arguments=normalized,
        )
        approvals = PostgresApprovalRepository(
            pool,
            approval_ttl_seconds=settings.nexus_approval_ttl_seconds,
        )
        lock_key = f"{actor_key}:governed-effect:{digest}"
        async with pool.connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                (lock_key,),
            )
            try:
                approval = await approvals.require_approved(
                    actor_key=actor_key,
                    action_digest=digest,
                    tool_name=tool_name,
                    target_boundary=target,
                    normalized_arguments=normalized,
                )
                try:
                    with governed_effect_capability(tool_name, digest):
                        result = await handler(request)
                except Exception as exc:
                    await approvals.record_execution(
                        approval,
                        status="failed",
                        error={"code": type(exc).__name__[:96], "tool_name": tool_name},
                    )
                    raise
                succeeded, evidence = _safe_execution_result(result, tool_name)
                await approvals.record_execution(
                    approval,
                    status="executed" if succeeded else "failed",
                    reference=evidence if succeeded else None,
                    error=None if succeeded else evidence,
                )
                return result
            except ApprovalConflict:
                raise
            finally:
                await connection.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                    (lock_key,),
                )


def governed_effect_middleware() -> GovernedEffectMiddleware:
    return GovernedEffectMiddleware()


__all__ = [
    "QUANT_EFFECT_TOOLS",
    "GovernedEffectDenied",
    "GovernedEffectMiddleware",
    "governed_effect_capability",
    "governed_effect_middleware",
    "require_governed_effect",
]
