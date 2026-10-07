"""Tools for the repo-owned AI-Trader worker."""

import json
from typing import Any, Literal

from langchain_core.tools import tool
from langchain_core.runnables import ensure_config

from source.application import GovernedPublicationService
from source.core.config import settings
from source.domain import PublicationConflict
from source.infrastructure.publication_repository import PostgresPublicationRepository
from source.infrastructure.runtime_resources import optional_domain_pool
from source.integrations.ai_trader_client import AiTraderClient, AiTraderError
from source.security import PermissionDenied, current_actor_key, require_permission


def _client() -> AiTraderClient | None:
    base_url = (settings.ai_trader_api_base_url or "").strip()
    if not base_url:
        return None
    return AiTraderClient(
        base_url,
        token=settings.ai_trader_token,
        timeout_seconds=settings.ai_trader_timeout_seconds,
    )


def _render(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _not_configured() -> str:
    return _render(
        {
            "ok": False,
            "error": {
                "code": "ai_trader_not_configured",
                "message": "AI_TRADER_API_BASE_URL is not configured.",
            },
        }
    )


def _forbidden(exc: PermissionDenied) -> str:
    return _render(
        {
            "ok": False,
            "error": {
                "code": "forbidden",
                "message": str(exc),
                "stage": "authorization",
            },
        }
    )


def _csv(values: list[str] | None) -> str | None:
    if not values:
        return None
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return ",".join(cleaned) if cleaned else None


def _evidence_ids(values: list[str] | None) -> list[str]:
    cleaned = sorted({str(value).strip() for value in (values or []) if str(value).strip()})
    if len(cleaned) > 50 or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value)
        for value in cleaned
    ):
        raise ValueError("Invalid evidence reference.")
    return cleaned


def _run_scope(action_digest_hint: str) -> tuple[str, str]:
    config = ensure_config()
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    thread_id = configurable.get("thread_id") or metadata.get("thread_id")
    run_id = config.get("run_id") or metadata.get("run_id")
    suffix = action_digest_hint[:24]
    return (
        str(thread_id or f"publication-thread-{suffix}"),
        str(run_id or f"publication-run-{suffix}"),
    )


async def _publish(
    *,
    client: AiTraderClient,
    publication_type: str,
    tool_name: str,
    request: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    pool = optional_domain_pool()
    publisher = (
        client.publish_strategy
        if publication_type == "strategy"
        else client.publish_discussion
    )
    if pool is None or not hasattr(pool, "connection"):
        if getattr(settings, "nexus_env", "development") == "production":
            raise RuntimeError("Domain control plane is unavailable.")
        return await publisher(request)

    actor_key = current_actor_key()
    thread_id, run_id = _run_scope(tool_name)
    service = GovernedPublicationService(PostgresPublicationRepository(pool))
    return await service.publish(
        actor_key=actor_key,
        publication_type=publication_type,
        tool_name=tool_name,
        normalized_arguments=request,
        thread_id=thread_id,
        run_id=run_id,
        publisher=publisher,
        idempotency_key=idempotency_key,
    )


class AiTraderTools:
    """Connector tools backed by the AI-Trader worker API."""

    @staticmethod
    @tool(parse_docstring=True)
    async def get_ai_trader_market_overview() -> str:
        """Read the AI-Trader market-intel overview snapshot."""
        try:
            require_permission("ai_trader:read")
        except PermissionDenied as exc:
            return _forbidden(exc)
        client = _client()
        if client is None:
            return _not_configured()
        try:
            return _render({"ok": True, "overview": await client.market_overview()})
        except AiTraderError as exc:
            return _render(exc.as_dict())

    @staticmethod
    @tool(parse_docstring=True)
    async def get_ai_trader_market_news(
        category: Literal["all", "equities", "macro", "crypto", "commodities"] = "all",
        limit: int = 5,
        symbols: str | None = None,
    ) -> str:
        """Read grouped AI-Trader market news snapshots.

        Args:
            category: Optional category filter, or "all" for every category.
            limit: Maximum items per category.
            symbols: Optional comma-separated ticker filter such as AAPL,MSFT,NVDA.
        """
        try:
            require_permission("ai_trader:read")
        except PermissionDenied as exc:
            return _forbidden(exc)
        client = _client()
        if client is None:
            return _not_configured()
        try:
            return _render(
                {
                    "ok": True,
                    "news": await client.market_news(
                        category=None if category == "all" else category,
                        limit=max(1, min(int(limit), 12)),
                        symbols=symbols,
                    ),
                }
            )
        except AiTraderError as exc:
            return _render(exc.as_dict())

    @staticmethod
    @tool(parse_docstring=True)
    async def get_ai_trader_signal_feed(
        market: str | None = None,
        message_type: Literal["all", "operation", "strategy", "discussion"] = "all",
        keyword: str | None = None,
        limit: int = 20,
    ) -> str:
        """Read AI-Trader social signal feed context.

        Args:
            market: Optional market filter such as us-stock, crypto, or polymarket.
            message_type: Optional signal type filter, or "all".
            keyword: Optional search keyword.
            limit: Maximum number of feed items.
        """
        try:
            require_permission("ai_trader:read")
        except PermissionDenied as exc:
            return _forbidden(exc)
        client = _client()
        if client is None:
            return _not_configured()
        try:
            return _render(
                {
                    "ok": True,
                    "feed": await client.signal_feed(
                        market=market,
                        message_type=None if message_type == "all" else message_type,
                        keyword=keyword,
                        limit=max(1, min(int(limit), 50)),
                    ),
                }
            )
        except AiTraderError as exc:
            return _render(exc.as_dict())

    @staticmethod
    @tool(parse_docstring=True)
    async def publish_ai_trader_strategy(
        market: str,
        title: str,
        content: str,
        symbols: list[str] | None = None,
        tags: list[str] | None = None,
        evidence_ids: list[str] | None = None,
    ) -> str:
        """Publish strategy analysis after the runtime approval interrupt resumes.

        Args:
            market: AI-Trader market key such as us-stock, crypto, or polymarket.
            title: Strategy title to publish.
            content: Strategy content, including research limitations.
            symbols: Optional related symbols.
            tags: Optional related tags.
            evidence_ids: Dataset, experiment, artifact, or source IDs supporting the action.
        """
        try:
            require_permission("ai_trader:publish")
        except PermissionDenied as exc:
            return _forbidden(exc)
        client = _client()
        if client is None:
            return _not_configured()
        request = {
            "market": market,
            "title": title,
            "content": content,
            "symbols": _csv(symbols),
            "tags": _csv(tags),
        }
        evidence = _evidence_ids(evidence_ids)
        if evidence:
            request["evidence_ids"] = evidence
        try:
            result = await _publish(
                client=client,
                publication_type="strategy",
                tool_name="publish_ai_trader_strategy",
                request=request,
                idempotency_key=None,
            )
            return _render({"ok": True, **result})
        except PublicationConflict as exc:
            return _render(
                {
                    "ok": False,
                    "error": {
                        "code": "publication_conflict",
                        "message": "Publication retry conflicts with another action.",
                        "stage": "idempotency",
                    },
                }
            )
        except AiTraderError as exc:
            return _render(exc.as_dict())
        except (RuntimeError, ValueError):
            return _render(
                {
                    "ok": False,
                    "error": {
                        "code": "publication_control_unavailable",
                        "message": "Governed publication control is unavailable.",
                        "stage": "control-plane",
                    },
                }
            )

    @staticmethod
    @tool(parse_docstring=True)
    async def publish_ai_trader_discussion(
        market: str,
        title: str,
        content: str,
        symbol: str | None = None,
        tags: list[str] | None = None,
        evidence_ids: list[str] | None = None,
    ) -> str:
        """Publish a discussion after the runtime approval interrupt resumes.

        Args:
            market: AI-Trader market key such as us-stock, crypto, or polymarket.
            title: Discussion title to publish.
            content: Discussion content, including research limitations.
            symbol: Optional primary symbol.
            tags: Optional related tags.
            evidence_ids: Dataset, experiment, artifact, or source IDs supporting the action.
        """
        try:
            require_permission("ai_trader:publish")
        except PermissionDenied as exc:
            return _forbidden(exc)
        client = _client()
        if client is None:
            return _not_configured()
        request = {
            "market": market,
            "title": title,
            "content": content,
            "symbol": symbol,
            "tags": _csv(tags),
        }
        evidence = _evidence_ids(evidence_ids)
        if evidence:
            request["evidence_ids"] = evidence
        try:
            result = await _publish(
                client=client,
                publication_type="discussion",
                tool_name="publish_ai_trader_discussion",
                request=request,
                idempotency_key=None,
            )
            return _render({"ok": True, **result})
        except PublicationConflict:
            return _render(
                {
                    "ok": False,
                    "error": {
                        "code": "publication_conflict",
                        "message": "Publication retry conflicts with another action.",
                        "stage": "idempotency",
                    },
                }
            )
        except AiTraderError as exc:
            return _render(exc.as_dict())
        except (RuntimeError, ValueError):
            return _render(
                {
                    "ok": False,
                    "error": {
                        "code": "publication_control_unavailable",
                        "message": "Governed publication control is unavailable.",
                        "stage": "control-plane",
                    },
                }
            )

    @staticmethod
    @tool(parse_docstring=True)
    async def poll_ai_trader_heartbeat() -> str:
        """Poll AI-Trader heartbeat for pending messages and tasks."""
        try:
            require_permission("ai_trader:read")
        except PermissionDenied as exc:
            return _forbidden(exc)
        client = _client()
        if client is None:
            return _not_configured()
        try:
            return _render({"ok": True, "heartbeat": await client.poll_heartbeat()})
        except AiTraderError as exc:
            return _render(exc.as_dict())

    tools: list[Any]


AiTraderTools.tools = [
    AiTraderTools.get_ai_trader_market_overview,
    AiTraderTools.get_ai_trader_market_news,
    AiTraderTools.get_ai_trader_signal_feed,
    AiTraderTools.publish_ai_trader_strategy,
    AiTraderTools.publish_ai_trader_discussion,
    AiTraderTools.poll_ai_trader_heartbeat,
]
