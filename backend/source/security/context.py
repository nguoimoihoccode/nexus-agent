"""Security context helpers shared by graphs and tools."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langgraph.runtime import get_runtime

from source.core.config import settings

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "user": frozenset({"chat:run", "research:read", "ai_trader:read"}),
    "analyst": frozenset(
        {
            "chat:run",
            "research:read",
            "ai_trader:read",
            "quant:read",
            "quant:data:write",
            "quant:experiment:run",
            "quant:interpretation:write",
        }
    ),
    "publisher": frozenset(
        {"chat:run", "research:read", "ai_trader:read", "ai_trader:publish"}
    ),
    "admin": frozenset(
        {
            "chat:run",
            "research:read",
            "ai_trader:read",
            "ai_trader:publish",
            "quant:read",
            "quant:data:write",
            "quant:experiment:run",
            "quant:interpretation:write",
            "studio:access",
            "maintenance:run",
        }
    ),
}

_database_actor: ContextVar[str | None] = ContextVar(
    "nexus_database_actor", default=None
)


class PermissionDenied(RuntimeError):
    """Raised when an authenticated actor lacks a required permission."""

    def __init__(self, permission: str) -> None:
        super().__init__(f"Permission '{permission}' is required.")
        self.permission = permission


def actor_key_for_subject(issuer: str, subject: str) -> str:
    """Return a stable pseudonymous owner key without persisting the OIDC subject."""
    secret = settings.nexus_tenant_hmac_key
    if secret is None:
        if settings.nexus_env == "production":
            raise RuntimeError("NEXUS_TENANT_HMAC_KEY is required in production.")
        key = b"nexus-development-actor-key"
    else:
        key = secret.get_secret_value().encode()
    digest = hmac.new(key, f"{issuer}\0{subject}".encode(), hashlib.sha256).hexdigest()
    return f"v1-{digest}"


def permissions_for_roles(roles: Iterable[str]) -> frozenset[str]:
    permissions: set[str] = set()
    for role in roles:
        permissions.update(ROLE_PERMISSIONS.get(str(role).strip().lower(), ()))
    return frozenset(permissions)


def _runtime_user() -> Any:
    try:
        runtime = get_runtime()
        return runtime.server_info.user
    except (AttributeError, KeyError, RuntimeError):
        return None


def current_actor_key() -> str:
    bound_actor = _database_actor.get()
    if bound_actor is not None:
        return bound_actor
    user = _runtime_user()
    identity = getattr(user, "identity", None)
    if identity:
        return str(identity)
    if settings.nexus_env != "production":
        return actor_key_for_subject("development", "local-user")
    raise PermissionDenied("chat:run")


@contextmanager
def database_actor_context(actor_key: str) -> Iterator[None]:
    """Bind one authenticated database principal to this task."""
    if not (
        actor_key.startswith("v1-")
        and len(actor_key) == 67
        and all(character in "0123456789abcdef" for character in actor_key[3:])
    ):
        raise ValueError("Invalid database actor key.")
    token = _database_actor.set(actor_key)
    try:
        yield
    finally:
        _database_actor.reset(token)


def current_permissions() -> frozenset[str]:
    user = _runtime_user()
    values = getattr(user, "permissions", None)
    if values is not None:
        return frozenset(str(value) for value in values)
    if settings.nexus_env != "production":
        return ROLE_PERMISSIONS["admin"]
    return frozenset()


def require_permission(permission: str) -> None:
    if permission not in current_permissions():
        raise PermissionDenied(permission)
