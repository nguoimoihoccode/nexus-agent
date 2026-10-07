"""Governed publication boundary with durable retry and approval evidence."""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from source.domain import (
    PublicationConflict,
    PublicationRepository,
    approval_action_identity,
)
from source.integrations.ai_trader_client import AiTraderError

Publisher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class GovernedPublicationService:
    def __init__(self, repository: PublicationRepository) -> None:
        self.repository = repository

    async def publish(
        self,
        *,
        actor_key: str,
        publication_type: str,
        tool_name: str,
        normalized_arguments: dict[str, Any],
        thread_id: str,
        run_id: str,
        publisher: Publisher,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        target_boundary, action_digest = approval_action_identity(
            actor_key=actor_key,
            tool_name=tool_name,
            normalized_arguments=normalized_arguments,
        )
        key = idempotency_key or f"publication:{action_digest[7:]}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,200}", key):
            raise ValueError("Invalid publication idempotency key.")
        effect, _created = await self.repository.reserve_approved_effect(
            actor_key=actor_key,
            publication_type=publication_type,
            tool_name=tool_name,
            target_boundary=target_boundary,
            normalized_arguments=normalized_arguments,
            action_digest=action_digest,
            idempotency_key=key,
            thread_id=thread_id,
            run_id=run_id,
        )
        if effect.status == "published" and effect.target_reference is not None:
            return {**effect.target_reference, "reused": True}

        worker_request = {
            **normalized_arguments,
            "actor_key": actor_key,
            "idempotency_key": effect.idempotency_key,
            "action_digest": action_digest,
        }
        try:
            target = await publisher(worker_request)
        except AiTraderError as exc:
            await self.repository.mark_failed(
                effect,
                safe_error={
                    "code": exc.code,
                    "stage": exc.stage,
                    "retryable": exc.retryable,
                },
            )
            raise
        published = await self.repository.mark_published(
            effect,
            thread_id=thread_id,
            run_id=run_id,
            target_reference=target,
        )
        if published.target_reference is None:
            raise PublicationConflict("publication_result_missing")
        return published.target_reference
