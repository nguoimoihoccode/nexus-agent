"""Application-owned tools and their runtime policies."""

import json
import logging
from contextvars import ContextVar
from time import perf_counter
from typing import Any

import httpx
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.runnables import ensure_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain.tools import tool

from source.core.config import settings
from source.core.observability import log_event
from source.infrastructure.event_repository import PostgresProductEventRepository
from source.infrastructure.runtime_resources import optional_domain_pool
from source.infrastructure.source_repository import (
    PostgresResearchSourceRepository,
    normalize_source_locator,
)
from source.security import PermissionDenied, current_actor_key, require_permission

logger = logging.getLogger(__name__)
_tool_evidence_context: ContextVar[dict[str, str]] = ContextVar(
    "tool_evidence_context", default={}
)


def format_web_search_results(
    payload: dict[str, Any],
    source_records: list[dict[str, Any]] | None = None,
) -> str:
    """Return a compact, citation-friendly search result summary."""
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return "No web search results were returned."

    lines: list[str] = []
    records_by_url = {
        item.get("url"): item for item in (source_records or []) if item.get("url")
    }
    for item in results[:5]:
        title = str(item.get("title") or "Untitled")
        url = str(item.get("url") or "")
        content = str(item.get("content") or "").strip()
        source = records_by_url.get(normalize_source_locator(url)) or {}
        evidence_id = source.get("source_record_id")
        evidence = f"\n  Evidence ID: {evidence_id}" if evidence_id else ""
        lines.append(f"- {title}\n  URL: {url}{evidence}\n  Extract: {content}")
    return "\n".join(lines)


@tool(parse_docstring=True)
async def web_search(query: str) -> str:
    """Search the public web and return source URLs with short extracts.

    Args:
        query: A focused search query suitable for a web search engine.
    """
    try:
        require_permission("research:read")
    except PermissionDenied as exc:
        return json.dumps(
            {"ok": False, "error": {"code": "forbidden", "message": str(exc)}},
            ensure_ascii=False,
        )
    api_key = settings.tavily_api_key
    config = ensure_config()
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    thread_id = str(configurable.get("thread_id") or metadata.get("thread_id") or "")
    run_id = str(config.get("run_id") or metadata.get("run_id") or "")
    pool = optional_domain_pool()
    repository = (
        PostgresResearchSourceRepository(pool)
        if pool is not None and hasattr(pool, "connection") and thread_id and run_id
        else None
    )
    evidence_context = _tool_evidence_context.get()
    if not api_key:
        if repository is not None:
            await repository.record_gap(
                actor_key=current_actor_key(),
                thread_id=thread_id,
                run_id=run_id,
                query=query,
                reason="search_not_configured",
                subagent_name=evidence_context.get("agent_name"),
                tool_call_id=evidence_context.get("tool_call_id"),
            )
        return (
            "Live web search is unavailable because TAVILY_API_KEY is not "
            "configured. Do not invent current facts or citations; state this "
            "limitation."
        )

    try:
        started_at = perf_counter()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key.get_secret_value(),
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 5,
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
            response.raise_for_status()
        log_event(
            logger,
            "web_search.completed",
            status_code=response.status_code,
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )
        payload = response.json()
        source_records = None
        if repository is not None:
            results = payload.get("results") if isinstance(payload, dict) else None
            source_records = await repository.record_search_results(
                actor_key=current_actor_key(),
                thread_id=thread_id,
                run_id=run_id,
                query=query,
                results=results if isinstance(results, list) else [],
                subagent_name=evidence_context.get("agent_name"),
                tool_call_id=evidence_context.get("tool_call_id"),
            )
        return format_web_search_results(payload, source_records)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        log_event(
            logger,
            "web_search.failed",
            level=logging.WARNING,
            exc_info=True,
            error_type=type(exc).__name__,
        )
        if repository is not None:
            await repository.record_gap(
                actor_key=current_actor_key(),
                thread_id=thread_id,
                run_id=run_id,
                query=query,
                reason="search_failed",
                subagent_name=evidence_context.get("agent_name"),
                tool_call_id=evidence_context.get("tool_call_id"),
            )
        return (
            f"Live web search failed ({type(exc).__name__}). "
            "Do not invent current facts or citations; state this limitation."
        )


class CustomTools:
    """Role-scoped application tools."""

    researcher = [web_search]


class ProductEventMiddleware(AgentMiddleware):
    """Emit bounded tool lifecycle events without copying arguments or results."""

    async def awrap_tool_call(self, request, handler):
        pool = optional_domain_pool()
        if pool is None or not hasattr(pool, "connection"):
            return await handler(request)
        config = ensure_config()
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        thread_id = configurable.get("thread_id") or metadata.get("thread_id")
        run_id = config.get("run_id") or metadata.get("run_id")
        tool_name = str(request.tool_call.get("name") or "unknown")
        tool_call_id = str(request.tool_call.get("id") or "unknown")
        agent_name = str(
            metadata.get("langgraph_node")
            or metadata.get("agent_name")
            or "unknown"
        )[:96]
        if not run_id:
            run_id = f"tool-{tool_call_id}"
        repository = PostgresProductEventRepository(pool)
        base = {
            "actor_key": current_actor_key(),
            "thread_id": str(thread_id) if thread_id else None,
            "run_id": str(run_id),
        }
        await repository.append(
            **base,
            event_type="agent.delegated" if tool_name == "task" else "tool.proposed",
            payload={"tool_call_id": tool_call_id, "tool_name": tool_name},
        )
        evidence_token = _tool_evidence_context.set(
            {"agent_name": agent_name, "tool_call_id": tool_call_id[:200]}
        )
        try:
            result = await handler(request)
        except Exception:
            await repository.append(
                **base,
                event_type="tool.failed",
                payload={"tool_call_id": tool_call_id, "tool_name": tool_name},
            )
            raise
        finally:
            _tool_evidence_context.reset(evidence_token)
        await repository.append(
            **base,
            event_type="tool.completed",
            payload={"tool_call_id": tool_call_id, "tool_name": tool_name},
        )
        return result


def product_event_middleware() -> ProductEventMiddleware:
    return ProductEventMiddleware()


class DelegationLimitMiddleware(AgentMiddleware):
    """Limit each subagent to one delegation per current user intent."""

    def __init__(self, *, max_total: int) -> None:
        self.max_total = max_total

    def after_model(self, state, runtime):
        del runtime
        messages = state.get("messages") or []
        last_user_index = max(
            (
                index
                for index, message in enumerate(messages)
                if isinstance(message, HumanMessage)
            ),
            default=-1,
        )
        last_ai_index = next(
            (
                index
                for index in range(len(messages) - 1, last_user_index, -1)
                if isinstance(messages[index], AIMessage)
            ),
            None,
        )
        if last_ai_index is None:
            return None
        current = messages[last_ai_index]
        if not isinstance(current, AIMessage):
            return None

        delegated: set[str] = set()
        total = 0
        for message in messages[last_user_index + 1 : last_ai_index]:
            if not isinstance(message, AIMessage):
                continue
            for tool_call in message.tool_calls:
                if tool_call.get("name") != "task":
                    continue
                total += 1
                subtype = str((tool_call.get("args") or {}).get("subagent_type") or "")
                if subtype:
                    delegated.add(subtype)

        blocked: list[ToolMessage] = []
        for tool_call in current.tool_calls:
            if tool_call.get("name") != "task":
                continue
            subtype = str((tool_call.get("args") or {}).get("subagent_type") or "")
            if total >= self.max_total or not subtype or subtype in delegated:
                blocked.append(
                    ToolMessage(
                        content=(
                            "Delegation limit reached. Do not delegate this agent again "
                            "for the current user request."
                        ),
                        tool_call_id=tool_call["id"],
                        name="task",
                        status="error",
                    )
                )
                continue
            total += 1
            delegated.add(subtype)
        return {"messages": blocked} if blocked else None

    async def aafter_model(self, state, runtime):
        return self.after_model(state, runtime)


class ToolExecutionPolicy:
    """Execution budgets for application and provider tools."""

    @staticmethod
    def researcher() -> list[Any]:
        return [
            ToolCallLimitMiddleware(
                tool_name="web_search",
                run_limit=settings.researcher_web_search_tool_call_limit,
                exit_behavior="continue",
            ),
            ModelCallLimitMiddleware(
                run_limit=settings.researcher_model_call_limit,
                exit_behavior="end",
            ),
            product_event_middleware(),
        ]

    @staticmethod
    def quant_researcher() -> list[Any]:
        return [
            ToolCallLimitMiddleware(
                tool_name="run_governed_qlib_experiment",
                run_limit=settings.quant_researcher_experiment_tool_call_limit,
                exit_behavior="continue",
            ),
            ToolCallLimitMiddleware(
                tool_name="record_experiment_interpretation",
                run_limit=settings.quant_researcher_interpretation_tool_call_limit,
                exit_behavior="continue",
            ),
            ToolCallLimitMiddleware(
                run_limit=settings.quant_researcher_tool_call_limit,
                exit_behavior="continue",
            ),
            ModelCallLimitMiddleware(
                run_limit=settings.quant_researcher_model_call_limit,
                exit_behavior="end",
            ),
            product_event_middleware(),
        ]

    @staticmethod
    def quant_data_agent() -> list[Any]:
        return [
            ToolCallLimitMiddleware(
                tool_name="prepare_qlib_dataset",
                run_limit=1,
                exit_behavior="continue",
            ),
            ToolCallLimitMiddleware(
                tool_name="fetch_factor_snapshot",
                run_limit=1,
                exit_behavior="continue",
            ),
            ToolCallLimitMiddleware(
                run_limit=settings.quant_data_agent_tool_call_limit,
                exit_behavior="continue",
            ),
            ModelCallLimitMiddleware(
                run_limit=settings.quant_data_agent_model_call_limit,
                exit_behavior="end",
            ),
            product_event_middleware(),
        ]

    @staticmethod
    def ai_trader_agent() -> list[Any]:
        return [
            ToolCallLimitMiddleware(
                tool_name="publish_ai_trader_strategy",
                run_limit=1,
                exit_behavior="continue",
            ),
            ToolCallLimitMiddleware(
                tool_name="publish_ai_trader_discussion",
                run_limit=1,
                exit_behavior="continue",
            ),
            ToolCallLimitMiddleware(
                run_limit=settings.ai_trader_agent_tool_call_limit,
                exit_behavior="continue",
            ),
            ModelCallLimitMiddleware(
                run_limit=settings.ai_trader_agent_model_call_limit,
                exit_behavior="end",
            ),
            product_event_middleware(),
        ]

    @staticmethod
    def supervisor() -> list[Any]:
        return [
            DelegationLimitMiddleware(max_total=settings.supervisor_task_call_limit),
            ToolCallLimitMiddleware(
                tool_name="task",
                run_limit=settings.supervisor_task_call_limit,
                exit_behavior="continue",
            ),
            ModelCallLimitMiddleware(
                run_limit=settings.supervisor_model_call_limit,
                exit_behavior="end",
            ),
        ]
