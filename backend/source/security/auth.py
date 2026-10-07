"""OIDC authentication and LangGraph resource authorization."""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid5

import httpx
import jwt
from langgraph_sdk import Auth

from source.core.config import settings
from source.core.observability import log_event
from source.security.context import actor_key_for_subject, permissions_for_roles

auth = Auth()
logger = logging.getLogger(__name__)

GRAPH_ASSISTANT_NAMESPACE = UUID("6ba7b821-9dad-11d1-80b4-00c04fd430c8")
SUPERVISOR_GRAPH = "supervisor"
STUDIO_GRAPHS = frozenset(
    {
        "researcher",
        "quant-data-agent",
        "quant-researcher",
        "ai-trader-agent",
    }
)


def assistant_id_for_graph(graph_name: str) -> str:
    """Return the server-owned deterministic assistant ID for one graph."""
    return str(uuid5(GRAPH_ASSISTANT_NAMESPACE, graph_name))


def _authorized_graph_name(value: dict[str, Any]) -> str | None:
    raw = str(value.get("assistant_id") or "")
    for graph_name in {SUPERVISOR_GRAPH, *STUDIO_GRAPHS}:
        if raw in {graph_name, assistant_id_for_graph(graph_name)}:
            return graph_name
    return None


def _http_error(status_code: int, detail: str) -> None:
    raise Auth.exceptions.HTTPException(status_code=status_code, detail=detail)


async def _discover_jwks_uri() -> str:
    if settings.nexus_oidc_jwks_uri:
        return settings.nexus_oidc_jwks_uri
    issuer = str(settings.nexus_oidc_issuer or "").rstrip("/")
    if not issuer:
        _http_error(503, "OIDC issuer is not configured.")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{issuer}/.well-known/openid-configuration")
            response.raise_for_status()
            jwks_uri = response.json().get("jwks_uri")
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise Auth.exceptions.HTTPException(
            status_code=503, detail="OIDC discovery is unavailable."
        ) from exc
    if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
        _http_error(503, "OIDC discovery returned an invalid JWKS URI.")
    return jwks_uri


@lru_cache(maxsize=4)
def _jwk_client(jwks_uri: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(jwks_uri, cache_keys=True, cache_jwk_set=True, lifespan=300)


async def _verify_token(token: str) -> dict[str, Any]:
    jwks_uri = await _discover_jwks_uri()
    try:
        signing_key = await asyncio.to_thread(
            _jwk_client(jwks_uri).get_signing_key_from_jwt, token
        )
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=settings.nexus_oidc_allowed_algorithms,
            audience=settings.nexus_oidc_audience,
            issuer=settings.nexus_oidc_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise Auth.exceptions.HTTPException(
            status_code=401, detail="Invalid or expired access token."
        ) from exc
    return claims


@auth.authenticate
async def authenticate(
    authorization: str | None,
    path: str,
    method: str,
    request: Any | None = None,
) -> Auth.types.MinimalUserDict:
    if method == "GET" and path == "/ok":
        return {"identity": "healthcheck", "permissions": []}
    if (method == "GET" and path == "/auth/login") or (
        method == "POST" and path in {"/auth/callback", "/auth/csp-report"}
    ):
        return {"identity": "oidc-bootstrap", "permissions": []}
    if settings.nexus_env != "production" and not settings.nexus_oidc_issuer:
        return {
            "identity": actor_key_for_subject("development", "local-user"),
            "permissions": sorted(permissions_for_roles(["admin"])),
        }
    browser_mode = getattr(settings, "nexus_browser_session_auth", "disabled")
    if browser_mode != "disabled" and request is not None:
        from source.infrastructure.runtime_resources import require_domain_pool
        from source.security.browser_session import (
            BROWSER_SESSION_COOKIE,
            BrowserSessionError,
            BrowserSessionManager,
        )

        session_token = request.cookies.get(BROWSER_SESSION_COOKIE)
        if session_token:
            manager = BrowserSessionManager(
                require_domain_pool(),
                app_settings=settings,
                verify_access_token=_verify_token,
            )
            try:
                if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                    origin = request.headers.get("origin")
                    if origin != settings.nexus_app_origin:
                        raise BrowserSessionError(403, "Request origin validation failed.")
                    fetch_site = request.headers.get("sec-fetch-site")
                    if fetch_site and fetch_site not in {"same-origin", "same-site"}:
                        raise BrowserSessionError(403, "Fetch Metadata validation failed.")
                    manager.validate_csrf(
                        session_token,
                        request.headers.get("x-nexus-csrf"),
                    )
                principal = await manager.resolve(session_token)
            except BrowserSessionError as exc:
                _http_error(exc.status_code, exc.detail)
            request.state.browser_principal = principal
            return {
                "identity": principal.actor_key,
                "permissions": list(principal.permissions),
            }
        if browser_mode == "required":
            _http_error(401, "Browser session is required.")
    if not authorization or not authorization.lower().startswith("bearer "):
        _http_error(401, "Bearer access token is required.")
    claims = await _verify_token(authorization[7:].strip())
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        _http_error(401, "Access token subject is missing.")
    raw_roles = claims.get(settings.nexus_oidc_roles_claim, [])
    roles = [raw_roles] if isinstance(raw_roles, str) else raw_roles
    if not isinstance(roles, list):
        _http_error(403, "Access token roles are invalid.")
    permissions = permissions_for_roles(str(role) for role in roles)
    if "chat:run" not in permissions:
        _http_error(403, "A recognized Nexus role is required.")
    actor_key = actor_key_for_subject(str(settings.nexus_oidc_issuer), subject)
    log_event(
        logger,
        "security.authentication.succeeded",
        actor_key=actor_key,
        permissions=sorted(permissions),
    )
    return {
        "identity": actor_key,
        "permissions": sorted(permissions),
    }


@auth.on
async def deny_unhandled(ctx: Auth.types.AuthContext, value: dict[str, Any]) -> bool:
    del ctx, value
    return False


def _owner_filter(ctx: Auth.types.AuthContext, value: dict[str, Any]) -> dict[str, str]:
    owner = str(ctx.user.identity)
    metadata = value.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["owner_key"] = owner
    return {"owner_key": owner}


@auth.on.threads.create
async def create_thread(ctx: Auth.types.AuthContext, value: dict[str, Any]):
    value["ttl"] = {"strategy": "delete", "ttl": 43_200}
    log_event(logger, "security.thread.created", actor_key=str(ctx.user.identity))
    return _owner_filter(ctx, value)


@auth.on.threads.read
@auth.on.threads.search
@auth.on.threads.update
@auth.on.threads.delete
async def access_thread(ctx: Auth.types.AuthContext, value: dict[str, Any]):
    return _owner_filter(ctx, value)


@auth.on.threads.create_run
async def create_run(ctx: Auth.types.AuthContext, value: dict[str, Any]):
    if "chat:run" not in ctx.permissions:
        return False
    graph_name = _authorized_graph_name(value)
    if graph_name is None:
        return False
    if graph_name in STUDIO_GRAPHS and "studio:access" not in ctx.permissions:
        return False
    try:
        _validate_run_payload(value)
    except ValueError as exc:
        raise Auth.exceptions.HTTPException(status_code=413, detail=str(exc)) from exc
    log_event(
        logger,
        "security.run.authorized",
        actor_key=str(ctx.user.identity),
        graph_name=graph_name,
    )
    return _owner_filter(ctx, value)


def _validate_run_payload(value: dict[str, Any]) -> None:
    kwargs = value.get("kwargs") or {}
    encoded = json.dumps(kwargs, ensure_ascii=False, default=str).encode()
    if len(encoded) > 64 * 1024:
        raise ValueError("Run input exceeds 64 KiB.")
    messages = (
        ((kwargs.get("input") or {}).get("messages") or [])
        if isinstance(kwargs, dict)
        else []
    )
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content", "")
            if len(str(content).encode()) > settings.nexus_prompt_max_bytes:
                raise ValueError(
                    f"User prompt exceeds {settings.nexus_prompt_max_bytes} bytes."
                )


@auth.on.assistants.read
@auth.on.assistants.search
async def read_assistants(ctx: Auth.types.AuthContext, value: dict[str, Any]):
    del value
    return "studio:access" in ctx.permissions
