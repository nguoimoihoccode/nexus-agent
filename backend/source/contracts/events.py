"""Versioned compact product-event envelopes and payload registry."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVENT_SCHEMA_VERSION = 1
MAX_EVENT_PAYLOAD_BYTES = 32_768


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunPayload(_Payload):
    graph_name: str = Field(min_length=1, max_length=128)


class ToolPayload(_Payload):
    tool_call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=128)


class ApprovalRequestedPayload(_Payload):
    approval_request_id: str
    action_digest: str
    tool_name: str
    target_boundary: str
    status: Literal["pending"]


class ApprovalDecidedPayload(_Payload):
    approval_request_id: str
    action_digest: str
    tool_name: str
    target_boundary: str
    decision: Literal["approve", "reject"]
    status: Literal["approved", "rejected"]
    decision_source: Literal["human", "authorization_lease"] = "human"


class ApprovalExpiredPayload(_Payload):
    approval_request_id: str
    action_digest: str
    tool_name: str
    target_boundary: str
    status: Literal["expired"]
    reason: Literal["approval_ttl_exceeded"]


class WorkflowTransitionPayload(_Payload):
    workflow_id: str
    workflow_type: Literal["prepare_qlib_dataset", "run_qlib_experiment"]
    from_state: str | None
    to_state: str
    stage: str
    error: dict[str, Any] | None = None


class WorkerLifecyclePayload(_Payload):
    worker: Literal["quant-worker"] = "quant-worker"
    job_type: Literal["qlib_experiment"] = "qlib_experiment"
    job_id: str
    status: Literal[
        "queued",
        "leased",
        "running",
        "cancelling",
        "cancelled",
        "completed",
        "failed",
        "expired",
        "requeued",
    ]
    stage: str | None = None
    recovery: bool = False
    reason_category: str | None = None


class StagingReadyPayload(_Payload):
    staging_revision_id: str
    content_hash: str


class FactorSnapshotReadyPayload(_Payload):
    factor_snapshot_revision_id: str


class DatasetReadyPayload(_Payload):
    dataset_revision_id: str
    manifest_hash: str
    dataset_alias: str


class MemoryUpdatedPayload(_Payload):
    tool_call_id: str
    operation: Literal["save", "delete"]
    changed: bool
    memory_key: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$",
    )
    revision: int | None = Field(default=None, gt=0)
    content_hash: str | None = None

    @model_validator(mode="after")
    def validate_operation_shape(self) -> MemoryUpdatedPayload:
        if self.operation == "save" and self.memory_key is None:
            raise ValueError("Memory save events require memory_key.")
        if self.operation == "delete" and self.memory_key is not None:
            raise ValueError("Memory delete events cannot include memory_key.")
        return self


class PublicationPublishedPayload(_Payload):
    publication_id: str
    publication_type: Literal["strategy", "discussion"]
    approval_request_id: str
    action_digest: str
    target_reference: dict[str, Any]


class ArtifactLifecyclePayload(_Payload):
    artifact_id: str
    producer_type: str
    producer_id: str
    status: Literal["ready", "expired", "deleted", "corrupted", "quarantined"]
    content_hash: str | None = None
    reason: str | None = None


EVENT_PAYLOAD_MODELS: dict[str, type[_Payload]] = {
    "agent.delegated": ToolPayload,
    "approval.decided": ApprovalDecidedPayload,
    "approval.expired": ApprovalExpiredPayload,
    "approval.requested": ApprovalRequestedPayload,
    "artifact.created": ArtifactLifecyclePayload,
    "artifact.deleted": ArtifactLifecyclePayload,
    "artifact.expired": ArtifactLifecyclePayload,
    "dataset.ready": DatasetReadyPayload,
    "factor_snapshot.ready": FactorSnapshotReadyPayload,
    "memory.updated": MemoryUpdatedPayload,
    "publication.published": PublicationPublishedPayload,
    "run.completed": RunPayload,
    "run.failed": RunPayload,
    "run.started": RunPayload,
    "staging.ready": StagingReadyPayload,
    "tool.completed": ToolPayload,
    "tool.failed": ToolPayload,
    "tool.proposed": ToolPayload,
    "workflow.transitioned": WorkflowTransitionPayload,
    "worker.accepted": WorkerLifecyclePayload,
    "worker.cancelled": WorkerLifecyclePayload,
    "worker.cancellation_requested": WorkerLifecyclePayload,
    "worker.completed": WorkerLifecyclePayload,
    "worker.expired": WorkerLifecyclePayload,
    "worker.failed": WorkerLifecyclePayload,
    "worker.leased": WorkerLifecyclePayload,
    "worker.requeued": WorkerLifecyclePayload,
    "worker.started": WorkerLifecyclePayload,
}
EVENT_TYPES = frozenset(EVENT_PAYLOAD_MODELS)

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|credential|password|secret|access[_-]?token|"
    r"refresh[_-]?token|raw[_-]?(?:prompt|content))",
    re.IGNORECASE,
)


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate, bound, and reject secret-shaped compact event payloads."""
    try:
        model = EVENT_PAYLOAD_MODELS[event_type]
    except KeyError as exc:
        raise ValueError(f"Unregistered product event type: {event_type}.") from exc
    normalized = model.model_validate(payload).model_dump(mode="json", exclude_none=True)
    _reject_sensitive_keys(normalized)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_EVENT_PAYLOAD_BYTES:
        raise ValueError("Product event payload exceeds the compact event limit.")
    return normalized


def _reject_sensitive_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                raise ValueError("Product event payload contains a sensitive field.")
            _reject_sensitive_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_keys(item)


class ProductEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(pattern=r"^evt_v1_[0-9a-f]{32}$")
    schema_version: Literal[1] = 1
    sequence: int = Field(gt=0)
    actor_key: str = Field(pattern=r"^v1-[0-9a-f]{64}$")
    thread_id: str | None = None
    run_id: str
    event_type: str
    occurred_at: str = Field(min_length=20, max_length=64)
    sensitivity: Literal["public", "internal", "restricted"] = "internal"
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_registered_payload(self) -> "ProductEventEnvelope":
        self.payload = validate_event_payload(self.event_type, self.payload)
        return self


class ReplayPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    events: list[ProductEventEnvelope]
    last_sequence: int = Field(ge=0)
    gap: bool = False
    retained_from_sequence: int | None = None
