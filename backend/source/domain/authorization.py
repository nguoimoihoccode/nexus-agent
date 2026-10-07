"""Actor-scoped authorization leases for governed product effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

AuthorizationMode = Literal["autonomous", "full_access"]

AUTONOMOUS_TOOL_NAMES = frozenset(
    {
        "prepare_qlib_dataset",
        "fetch_factor_snapshot",
        "run_governed_qlib_experiment",
        "record_experiment_interpretation",
    }
)
SENSITIVE_TOOL_NAMES = frozenset(
    {
        "delete_user_memory",
        "publish_ai_trader_strategy",
        "publish_ai_trader_discussion",
    }
)
GOVERNED_TOOL_NAMES = frozenset(
    {
        *AUTONOMOUS_TOOL_NAMES,
        *SENSITIVE_TOOL_NAMES,
        "save_user_memory",
    }
)


@dataclass(frozen=True)
class AuthorizationLease:
    actor_key: str
    lease_id: str
    thread_id: str
    mode: AuthorizationMode
    allow_sensitive: bool
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        now = datetime.now(timezone.utc)
        expires_at = (
            self.expires_at
            if self.expires_at.tzinfo
            else self.expires_at.replace(tzinfo=timezone.utc)
        )
        return self.revoked_at is None and expires_at > now

    def allows(self, tool_name: str) -> bool:
        if not self.active or tool_name not in GOVERNED_TOOL_NAMES:
            return False
        if self.mode == "autonomous":
            return tool_name in AUTONOMOUS_TOOL_NAMES
        return self.allow_sensitive or tool_name not in SENSITIVE_TOOL_NAMES

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return tuple(sorted(name for name in GOVERNED_TOOL_NAMES if self.allows(name)))


__all__ = [
    "AUTONOMOUS_TOOL_NAMES",
    "GOVERNED_TOOL_NAMES",
    "SENSITIVE_TOOL_NAMES",
    "AuthorizationLease",
    "AuthorizationMode",
]
