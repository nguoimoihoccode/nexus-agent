"""Framework-independent Nexus domain contracts."""

from source.domain.approval import (
    ApprovalConflict,
    ApprovalRecord,
    approval_action_identity,
    normalize_governed_arguments,
)
from source.domain.authorization import (
    AUTONOMOUS_TOOL_NAMES,
    GOVERNED_TOOL_NAMES,
    SENSITIVE_TOOL_NAMES,
    AuthorizationLease,
    AuthorizationMode,
)

from source.domain.control_plane import (
    DomainControlRepository,
    IdempotencyConflict,
    IdempotencyDecision,
    ProductEvent,
)
from source.domain.memory import normalize_memory_key
from source.domain.publication import (
    PublicationConflict,
    PublicationEffect,
    PublicationRepository,
)
from source.domain.workflow import (
    WorkflowConflict,
    WorkflowRecord,
    WorkflowRepository,
    WorkflowType,
)

__all__ = [
    "ApprovalConflict",
    "ApprovalRecord",
    "AUTONOMOUS_TOOL_NAMES",
    "AuthorizationLease",
    "AuthorizationMode",
    "DomainControlRepository",
    "IdempotencyConflict",
    "IdempotencyDecision",
    "GOVERNED_TOOL_NAMES",
    "ProductEvent",
    "PublicationConflict",
    "PublicationEffect",
    "PublicationRepository",
    "SENSITIVE_TOOL_NAMES",
    "WorkflowConflict",
    "WorkflowRecord",
    "WorkflowRepository",
    "WorkflowType",
    "approval_action_identity",
    "normalize_governed_arguments",
    "normalize_memory_key",
]
