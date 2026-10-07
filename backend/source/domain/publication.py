"""Durable approval and publication-effect contracts."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol


class PublicationConflict(RuntimeError):
    """A publication retry key no longer identifies the same approved action."""


@dataclass(frozen=True)
class PublicationEffect:
    actor_key: str
    publication_id: str
    publication_type: str
    approval_request_id: str
    action_digest: str
    idempotency_key: str
    status: Literal["reserved", "published", "failed", "rejected"]
    target_reference: dict[str, Any] | None = None
    safe_error: dict[str, Any] | None = None


class PublicationRepository(Protocol):
    async def reserve_approved_effect(
        self,
        *,
        actor_key: str,
        publication_type: str,
        tool_name: str,
        target_boundary: str,
        normalized_arguments: dict[str, Any],
        action_digest: str,
        idempotency_key: str,
        thread_id: str,
        run_id: str,
    ) -> tuple[PublicationEffect, bool]: ...

    async def mark_published(
        self,
        effect: PublicationEffect,
        *,
        thread_id: str,
        run_id: str,
        target_reference: dict[str, Any],
    ) -> PublicationEffect: ...

    async def mark_failed(
        self,
        effect: PublicationEffect,
        *,
        safe_error: dict[str, Any],
    ) -> PublicationEffect: ...
