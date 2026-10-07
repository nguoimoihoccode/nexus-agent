"""Internal service authentication and actor scoping."""

from __future__ import annotations

import os
import re
import secrets
from contextvars import ContextVar
from contextlib import contextmanager

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

_actor: ContextVar[str] = ContextVar("nexus_actor_key", default="development")
_actor_pattern = re.compile(r"^v1-[0-9a-f]{64}$")


def current_actor_key() -> str:
    return _actor.get()


def bind_database_actor(connection) -> None:
    actor = current_actor_key()
    database_actor = actor if _actor_pattern.fullmatch(actor) else ""
    connection.execute(
        "SELECT set_config('nexus.actor_key', %s, false)", (database_actor,)
    )
    connection.commit()


def reset_database_actor(connection) -> None:
    connection.execute("RESET nexus.actor_key")
    connection.commit()


def install_worker_auth(app: FastAPI) -> None:
    @app.middleware("http")
    async def authenticate_internal_request(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        expected = os.environ.get("NEXUS_QUANT_WORKER_TOKEN", "").strip()
        environment = os.environ.get("NEXUS_ENV", "development")
        if environment == "production" and len(expected) < 32:
            return JSONResponse(
                {"detail": "NEXUS_QUANT_WORKER_TOKEN is not securely configured."},
                status_code=503,
            )
        authorization = request.headers.get("authorization", "").strip()
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if expected and (not supplied or not secrets.compare_digest(supplied, expected)):
            return JSONResponse({"detail": "Invalid worker token."}, status_code=401)
        actor = request.headers.get("x-nexus-actor-key", "").strip()
        operator_path = request.url.path == "/ready"
        if environment == "production" and not operator_path and not _actor_pattern.fullmatch(actor):
            return JSONResponse({"detail": "Invalid actor key."}, status_code=422)
        if actor and not _actor_pattern.fullmatch(actor):
            return JSONResponse({"detail": "Invalid actor key."}, status_code=422)
        token = _actor.set(actor or "development")
        try:
            return await call_next(request)
        finally:
            _actor.reset(token)


def actor_directory(root):
    actor = current_actor_key()
    return root / actor if actor.startswith("v1-") else root


@contextmanager
def actor_context(actor: str):
    """Bind an actor while performing non-request maintenance work."""
    if actor != "development" and not _actor_pattern.fullmatch(actor):
        raise ValueError("Invalid actor key")
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)
