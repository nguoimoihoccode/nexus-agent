"""Signal publishing, feed, and heartbeat use cases."""

from typing import Any

from worker.domain.market import normalize_values, utc_now_iso
from worker.models import DiscussionRequest, StrategyRequest
from worker.ports import SignalRepository


class SignalService:
    def __init__(self, repository: SignalRepository) -> None:
        self.repository = repository

    def publish_strategy(self, request: StrategyRequest) -> dict[str, Any]:
        return self.repository.publish_signal(
            message_type="strategy",
            market=request.market,
            title=request.title,
            content=request.content,
            symbol=None,
            symbols=normalize_values(request.symbols),
            tags=normalize_values(request.tags),
            actor_key=request.actor_key,
            idempotency_key=request.idempotency_key,
            action_digest=request.action_digest,
        )

    def publish_discussion(self, request: DiscussionRequest) -> dict[str, Any]:
        return self.repository.publish_signal(
            message_type="discussion",
            market=request.market,
            title=request.title,
            content=request.content,
            symbol=request.symbol,
            symbols=[],
            tags=normalize_values(request.tags),
            actor_key=request.actor_key,
            idempotency_key=request.idempotency_key,
            action_digest=request.action_digest,
        )

    def signal_feed(self, **filters: Any) -> dict[str, Any]:
        return self.repository.signal_feed(**filters)

    @staticmethod
    def heartbeat() -> dict[str, Any]:
        return {
            "agent_id": "nexus-agent",
            "server_time": utc_now_iso(),
            "recommended_poll_interval_seconds": 30,
            "messages": [],
            "tasks": [],
            "message_count": 0,
            "task_count": 0,
            "unread_count": 0,
            "remaining_unread_count": 0,
            "remaining_task_count": 0,
            "has_more_messages": False,
            "has_more_tasks": False,
        }
