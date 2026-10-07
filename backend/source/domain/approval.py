"""Approval identities and durable decision records."""

from dataclasses import dataclass
from typing import Any, Literal

from source.contracts.identity import action_fingerprint, content_hash
from source.domain.memory import normalize_memory_key


class ApprovalConflict(RuntimeError):
    """An approval identity was reused with conflicting action or decision data."""


@dataclass(frozen=True)
class ApprovalRecord:
    actor_key: str
    approval_request_id: str
    thread_id: str
    run_id: str
    action_digest: str
    tool_name: str
    target_boundary: str
    normalized_arguments: dict[str, Any]
    status: Literal["pending", "approved", "rejected", "expired"]


def approval_action_identity(
    *,
    actor_key: str,
    tool_name: str,
    normalized_arguments: dict[str, Any],
) -> tuple[str, str]:
    normalized_arguments = normalize_governed_arguments(
        tool_name,
        normalized_arguments,
    )
    publication_targets = {
        "publish_ai_trader_strategy": "ai-trader-worker:signals/strategy",
        "publish_ai_trader_discussion": "ai-trader-worker:signals/discussion",
        "prepare_qlib_dataset": "quant-data-worker:dataset",
        "fetch_factor_snapshot": "quant-data-worker:factor-snapshot",
        "run_governed_qlib_experiment": "quant-worker:experiment",
        "record_experiment_interpretation": "postgres:interpretation",
    }
    if tool_name in {"save_user_memory", "delete_user_memory"}:
        target_boundary = "postgres:user-memory"
    else:
        try:
            target_boundary = publication_targets[tool_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported governed approval tool: {tool_name}.") from exc
    digest = action_fingerprint(
        {
            "actor_key": actor_key,
            "arguments": normalized_arguments,
            "schema_version": "1",
            "target_boundary": target_boundary,
            "tool_name": tool_name,
        }
    )
    return target_boundary, digest


def normalize_governed_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    if tool_name == "save_user_memory":
        memory_key = normalize_memory_key(arguments.get("memory_key"))
        if "content_hash" in arguments and "content_bytes" in arguments:
            normalized = {
                "memory_key": memory_key,
                "content_hash": arguments["content_hash"],
                "content_bytes": arguments["content_bytes"],
            }
        else:
            content = str(arguments.get("content") or "").strip()
            normalized = {
                "memory_key": memory_key,
                "content_hash": content_hash("user_memory", content),
                "content_bytes": len(content.encode("utf-8")),
            }
        return _with_memory_call_identity(
            normalized,
            arguments,
            tool_call_id,
        )
    if tool_name == "delete_user_memory":
        return _with_memory_call_identity({}, arguments, tool_call_id)
    if tool_name == "publish_ai_trader_strategy":
        normalized = {
            "market": arguments.get("market"),
            "title": arguments.get("title"),
            "content": arguments.get("content"),
            "symbols": _csv(arguments.get("symbols")),
            "tags": _csv(arguments.get("tags")),
        }
        return _with_evidence(normalized, arguments.get("evidence_ids"))
    if tool_name == "publish_ai_trader_discussion":
        normalized = {
            "market": arguments.get("market"),
            "title": arguments.get("title"),
            "content": arguments.get("content"),
            "symbol": arguments.get("symbol"),
            "tags": _csv(arguments.get("tags")),
        }
        return _with_evidence(normalized, arguments.get("evidence_ids"))
    if tool_name == "prepare_qlib_dataset":
        normalized = {
            "symbols": _sorted_strings(arguments.get("symbols")),
            "start_date": _scalar(arguments.get("start_date")),
            "end_date": _scalar(arguments.get("end_date")),
            "dataset_alias": arguments.get("dataset_alias"),
            "universe_name": arguments.get("universe_name"),
            "provider": arguments.get("provider", "yfinance"),
            "frequency": arguments.get("frequency", "1d"),
            "adjustment": arguments.get("adjustment", "auto"),
            "include_fields": _sorted_strings(arguments.get("include_fields")),
        }
        return _with_tool_call_identity(normalized, arguments, tool_call_id)
    if tool_name == "fetch_factor_snapshot":
        normalized = {
            "symbols": _sorted_strings(arguments.get("symbols")),
            "as_of_date": _scalar(arguments.get("as_of_date")),
            "provider": arguments.get("provider"),
        }
        return _with_tool_call_identity(normalized, arguments, tool_call_id)
    if tool_name == "run_governed_qlib_experiment":
        normalized = {
            key: _scalar(arguments.get(key))
            for key in (
                "train_start",
                "train_end",
                "valid_start",
                "valid_end",
                "test_start",
                "test_end",
                "dataset_revision_id",
                "universe",
                "model",
                "topk",
                "n_drop",
                "account",
                "open_cost",
                "close_cost",
                "min_cost",
            )
        }
        normalized.update(
            {
                "model": arguments.get("model", "lightgbm"),
                "topk": arguments.get("topk", 50),
                "n_drop": arguments.get("n_drop", 5),
                "account": arguments.get("account", 100_000_000),
                "open_cost": arguments.get("open_cost", 0.0005),
                "close_cost": arguments.get("close_cost", 0.0015),
                "min_cost": arguments.get("min_cost", 5),
            }
        )
        return _with_tool_call_identity(normalized, arguments, tool_call_id)
    if tool_name == "record_experiment_interpretation":
        normalized = {
            "experiment_id": arguments.get("experiment_id"),
            "summary": arguments.get("summary"),
            "findings": _normalized_findings(arguments.get("findings")),
            "evidence_ids": _sorted_strings(arguments.get("evidence_ids")),
        }
        return _with_tool_call_identity(normalized, arguments, tool_call_id)
    return dict(arguments)


def _with_memory_call_identity(
    normalized: dict[str, Any],
    arguments: dict[str, Any],
    tool_call_id: str | None,
) -> dict[str, Any]:
    call_hash = (
        content_hash("memory_tool_call", tool_call_id)
        if tool_call_id
        else arguments.get("tool_call_hash")
    )
    return {**normalized, "tool_call_hash": call_hash} if call_hash else normalized


def _with_tool_call_identity(
    normalized: dict[str, Any],
    arguments: dict[str, Any],
    tool_call_id: str | None,
) -> dict[str, Any]:
    call_hash = (
        content_hash("governed_tool_call", tool_call_id)
        if tool_call_id
        else arguments.get("tool_call_hash")
    )
    return {**normalized, "tool_call_hash": call_hash} if call_hash else normalized


def _scalar(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _sorted_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _normalized_findings(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    findings = [
        {
            "metric_id": str(item.get("metric_id") or ""),
            "statement": str(item.get("statement") or ""),
        }
        for item in value
        if isinstance(item, dict)
    ]
    return sorted(findings, key=lambda item: (item["metric_id"], item["statement"]))


def _csv(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip()) or None
    return str(value)


def _with_evidence(values: dict[str, Any], evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, list):
        return values
    normalized = sorted(
        {str(item).strip() for item in evidence if str(item).strip()}
    )[:50]
    return {**values, "evidence_ids": normalized} if normalized else values
