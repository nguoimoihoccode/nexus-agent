"""Framework-independent durable workflow state and repository port."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

WorkflowType = Literal["prepare_qlib_dataset", "run_qlib_experiment"]


@dataclass(frozen=True)
class WorkflowRecord:
    actor_key: str
    workflow_id: str
    workflow_type: WorkflowType
    idempotency_key: str
    intent_digest: str
    state: str
    stage: str
    normalized_input: dict[str, Any]
    output: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


class WorkflowConflict(ValueError):
    """An idempotency key was reused for a different workflow intent."""


class WorkflowRepository(Protocol):
    async def start(
        self,
        *,
        actor_key: str,
        workflow_type: WorkflowType,
        idempotency_key: str,
        intent_digest: str,
        normalized_input: dict[str, Any],
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[WorkflowRecord, bool]: ...

    async def transition(
        self,
        record: WorkflowRecord,
        *,
        allowed_from: set[str],
        to_state: str,
        stage: str,
        output_patch: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> WorkflowRecord: ...
