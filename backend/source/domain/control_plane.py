"""Ports and immutable records for durable idempotency and product events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


class IdempotencyConflict(ValueError):
    """One idempotency key was reused with different normalized arguments."""


@dataclass(frozen=True)
class IdempotencyDecision:
    actor_key: str
    operation: str
    idempotency_key: str
    arguments_digest: str
    state: Literal["reserved", "completed", "failed", "rejected"]
    result_reference: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProductEvent:
    actor_key: str
    event_id: str
    schema_version: int
    thread_id: str | None
    run_id: str
    sequence: int
    event_type: str
    sensitivity: str
    payload: dict[str, Any]


class DomainControlRepository(Protocol):
    async def reserve_idempotency(
        self,
        *,
        actor_key: str,
        operation: str,
        idempotency_key: str,
        arguments_digest: str,
    ) -> tuple[IdempotencyDecision, bool]: ...

    async def complete_idempotency_with_event(
        self,
        *,
        decision: IdempotencyDecision,
        result_reference: dict[str, Any],
        thread_id: str | None,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> ProductEvent: ...
