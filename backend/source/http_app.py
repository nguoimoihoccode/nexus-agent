"""Authenticated product routes mounted into the LangGraph API server."""

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from source.agents.agent import _prepare_server_resources
from source.agents.config.registry import frontend_safe_topology
from source.contracts import ReplayPage
from source.core.config import settings
from source.core.observability import log_event
from source.infrastructure.authorization_repository import (
    PostgresAuthorizationLeaseRepository,
)
from source.infrastructure.event_repository import PostgresProductEventRepository
from source.infrastructure.experiment_repository import (
    EXPERIMENT_STATUSES,
    PostgresExperimentCatalogRepository,
)
from source.infrastructure.lineage_repository import PostgresLineageRepository
from source.infrastructure.memory_repository import PostgresUserMemoryRepository
from source.infrastructure.runtime_resources import require_domain_pool
from source.integrations.qlib_client import QlibClient, QuantWorkerError
from source.security import database_actor_context
from source.security.auth import _verify_token
from source.security.browser_session import (
    BROWSER_LOGIN_COOKIE,
    BROWSER_SESSION_COOKIE,
    BrowserSessionError,
    BrowserSessionManager,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_LOCAL_CSRF_TOKEN = str()
logger = logging.getLogger(__name__)


class AuthorizationLeaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    thread_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,200}$")
    mode: Literal["autonomous", "full_access"]
    ttl_seconds: int = Field(default=3600, ge=300, le=14_400)
    allow_sensitive: bool = False

    @model_validator(mode="after")
    def validate_sensitive_mode(self) -> "AuthorizationLeaseInput":
        if self.mode != "full_access" and self.allow_sensitive:
            raise ValueError("Sensitive auto-approval requires full access mode.")
        return self


def _lease_payload(thread_id: str, lease=None) -> dict:
    if lease is None:
        return {
            "lease_id": None,
            "thread_id": thread_id,
            "mode": "safe",
            "allow_sensitive": False,
            "allowed_tools": [],
            "created_at": None,
            "expires_at": None,
        }
    return {
        "lease_id": lease.lease_id,
        "thread_id": lease.thread_id,
        "mode": lease.mode,
        "allow_sensitive": lease.allow_sensitive,
        "allowed_tools": list(lease.allowed_tools),
        "created_at": lease.created_at,
        "expires_at": lease.expires_at,
    }


def _identity(request: Request) -> tuple[str, frozenset[str]]:
    user = request.scope.get("user")
    actor_key = str(getattr(user, "identity", ""))
    auth = request.scope.get("auth")
    # Starlette exposes AuthCredentials here, not a bare iterable.  Supporting
    # both keeps the custom routes compatible with test ASGI scopes as well.
    raw_permissions = getattr(auth, "scopes", auth or ())
    permissions = frozenset(str(item) for item in raw_permissions)
    if not re.fullmatch(r"v1-[0-9a-f]{64}", actor_key):
        raise HTTPException(status_code=401, detail="Authenticated actor is required.")
    return actor_key, permissions


def _require_permission(permissions: frozenset[str], permission: str) -> None:
    if permission not in permissions:
        raise HTTPException(status_code=403, detail=f"Permission '{permission}' is required.")


async def _outbox_loop(stop: asyncio.Event) -> None:
    repository = PostgresProductEventRepository(
        require_domain_pool(),
        replay_retention_seconds=settings.nexus_replay_retention_seconds,
    )
    delay = settings.nexus_replay_poll_milliseconds / 1000
    while not stop.is_set():
        try:
            projected = await repository.project_outbox()
        except Exception as exc:
            projected = 0
            log_event(
                logger,
                "product_event.projection_failed",
                level=logging.ERROR,
                error_type=type(exc).__name__,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=0 if projected else delay)
        except TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _prepare_server_resources()
    stop = asyncio.Event()
    task = asyncio.create_task(_outbox_loop(stop), name="product-event-outbox")
    try:
        yield
    finally:
        stop.set()
        await task


app = FastAPI(
    title="Nexus Product API",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _browser_session_manager() -> BrowserSessionManager:
    return BrowserSessionManager(
        require_domain_pool(),
        app_settings=settings,
        verify_access_token=_verify_token,
    )


@app.get("/auth/login", include_in_schema=False)
async def browser_login(return_to: str = Query(default="/chat", max_length=2048)):
    if settings.nexus_browser_session_auth == "disabled":
        return RedirectResponse(url="/chat", status_code=303)
    try:
        login = await _browser_session_manager().start_login(return_to)
    except BrowserSessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    response = RedirectResponse(url=login.authorization_url, status_code=303)
    response.set_cookie(
        BROWSER_LOGIN_COOKIE,
        login.browser_binding,
        max_age=600,
        secure=settings.nexus_env == "production",
        httponly=True,
        samesite="none",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store, private"
    return response


@app.post("/auth/callback", include_in_schema=False)
async def browser_callback(request: Request):
    if settings.nexus_browser_session_auth == "disabled":
        raise HTTPException(status_code=404, detail="Browser session authentication is disabled.")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="OIDC callback content type is invalid.")
    body = await request.body()
    if not body or len(body) > 8 * 1024:
        raise HTTPException(status_code=400, detail="OIDC callback payload is invalid.")
    try:
        fields = parse_qs(
            body.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=6,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="OIDC callback payload is invalid.") from exc
    allowed_fields = {"state", "code", "iss", "session_state"}
    if (
        not {"state", "code"}.issubset(fields)
        or not set(fields).issubset(allowed_fields)
        or any(len(values) != 1 for values in fields.values())
    ):
        raise HTTPException(status_code=400, detail="OIDC callback fields are invalid.")
    state = fields["state"][0]
    code = fields["code"][0]
    if not state or len(state) > 256 or not code or len(code) > 4096:
        raise HTTPException(status_code=400, detail="OIDC callback fields are invalid.")
    if "iss" in fields and fields["iss"][0].rstrip("/") != str(settings.nexus_oidc_issuer).rstrip("/"):
        raise HTTPException(status_code=400, detail="OIDC callback issuer is invalid.")
    if "session_state" in fields and len(fields["session_state"][0]) > 1024:
        raise HTTPException(status_code=400, detail="OIDC callback session state is invalid.")
    browser_binding = request.cookies.get(BROWSER_LOGIN_COOKIE, "")
    try:
        login = await _browser_session_manager().complete_login(
            state=state,
            code=code,
            browser_binding=browser_binding,
        )
    except BrowserSessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    response = RedirectResponse(url=login.return_to, status_code=303)
    response.set_cookie(
        BROWSER_SESSION_COOKIE,
        login.session_token,
        max_age=settings.nexus_browser_session_absolute_seconds,
        secure=settings.nexus_env == "production",
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        BROWSER_LOGIN_COOKIE,
        secure=settings.nexus_env == "production",
        httponly=True,
        samesite="none",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store, private"
    return response


@app.get("/auth/session", include_in_schema=False)
async def browser_session(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    if settings.nexus_browser_session_auth == "disabled":
        return {
            "authenticated": True,
            "auth_mode": "local",
            "session_key": "local",
            "csrf_token": _LOCAL_CSRF_TOKEN,
        }
    principal = getattr(request.state, "browser_principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="Browser session is required.")
    return {
        "authenticated": True,
        "auth_mode": "browser",
        "session_key": principal.session_key,
        "csrf_token": principal.csrf_token,
    }


@app.post("/auth/logout", include_in_schema=False)
async def browser_logout(request: Request):
    logout_url = None
    session_token = request.cookies.get(BROWSER_SESSION_COOKIE)
    if settings.nexus_browser_session_auth != "disabled" and session_token:
        try:
            logout_url = await _browser_session_manager().logout(session_token)
        except BrowserSessionError:
            logout_url = None
    response = JSONResponse({"logout_url": logout_url})
    response.delete_cookie(
        BROWSER_SESSION_COOKIE,
        secure=settings.nexus_env == "production",
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    return response


@app.post("/auth/csp-report", status_code=204, include_in_schema=False)
async def browser_csp_report(request: Request):
    body = await request.body()
    if len(body) > 16 * 1024:
        raise HTTPException(status_code=413, detail="CSP report is too large.")
    try:
        raw = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="CSP report is invalid.") from exc
    report = raw.get("csp-report", raw) if isinstance(raw, dict) else {}
    if isinstance(report, dict):
        log_event(
            logger,
            "security.csp_violation",
            violated_directive=str(report.get("violated-directive") or "")[:100],
            effective_directive=str(report.get("effective-directive") or "")[:100],
            disposition=str(report.get("disposition") or "")[:20],
        )
    return Response(status_code=204)


@app.middleware("http")
async def bind_database_actor(request: Request, call_next):
    user = request.scope.get("user")
    actor_key = str(getattr(user, "identity", ""))
    if re.fullmatch(r"v1-[0-9a-f]{64}", actor_key):
        with database_actor_context(actor_key):
            return await call_next(request)
    return await call_next(request)


@app.get("/v1/runs/{run_id}/events", response_model=ReplayPage)
async def replay_events(
    request: Request,
    run_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "chat:run")
    if not _SAFE_ID.fullmatch(run_id):
        raise HTTPException(status_code=422, detail="Invalid run ID.")
    repository = PostgresProductEventRepository(
        require_domain_pool(),
        replay_retention_seconds=settings.nexus_replay_retention_seconds,
    )
    with database_actor_context(actor_key):
        await repository.project_outbox()
        return await repository.fetch_after(
            actor_key=actor_key,
            run_id=run_id,
            after=after,
            limit=limit,
        )


@app.get("/v1/topology")
async def runtime_topology(request: Request) -> dict:
    _actor_key, permissions = _identity(request)
    _require_permission(permissions, "chat:run")
    # The registry is re-read from disk on every request -- globbing
    # .deepagents/skills and reading each SKILL.md and AGENTS.md -- so this
    # belongs on a worker thread. `langgraph dev` runs the event loop under
    # blockbuster, which raises BlockingError rather than merely stalling when
    # that scan happens inline, and the endpoint answered 500 on the demo host
    # for exactly that reason.
    return await asyncio.to_thread(frontend_safe_topology)


@app.get("/v1/memory")
async def get_user_memory(request: Request, response: Response) -> dict:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "chat:run")
    response.headers["Cache-Control"] = "no-store, private"
    with database_actor_context(actor_key):
        memory = await PostgresUserMemoryRepository(require_domain_pool()).get(actor_key)
    if memory is None:
        return {"content": "", "revision": 0, "updated_at": None}
    return {
        "content": memory.content,
        "revision": memory.revision,
        "updated_at": memory.updated_at,
    }


@app.get("/v1/authorization-leases/current")
async def get_authorization_lease(
    request: Request,
    response: Response,
    thread_id: str = Query(pattern=r"^[A-Za-z0-9._:-]{1,200}$"),
) -> dict:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "chat:run")
    response.headers["Cache-Control"] = "no-store, private"
    lease = await PostgresAuthorizationLeaseRepository(
        require_domain_pool()
    ).get_active(actor_key=actor_key, thread_id=thread_id)
    return _lease_payload(thread_id, lease)


@app.post("/v1/authorization-leases")
async def create_authorization_lease(
    body: AuthorizationLeaseInput,
    request: Request,
    response: Response,
) -> dict:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "chat:run")
    response.headers["Cache-Control"] = "no-store, private"
    lease = await PostgresAuthorizationLeaseRepository(
        require_domain_pool()
    ).create(
        actor_key=actor_key,
        thread_id=body.thread_id,
        mode=body.mode,
        ttl_seconds=body.ttl_seconds,
        allow_sensitive=body.allow_sensitive,
    )
    return _lease_payload(body.thread_id, lease)


@app.delete("/v1/authorization-leases/{lease_id}", status_code=204)
async def revoke_authorization_lease(request: Request, lease_id: str) -> Response:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "chat:run")
    if not re.fullmatch(r"azl_v1_[0-9a-f]{32}", lease_id):
        raise HTTPException(status_code=422, detail="Invalid authorization lease ID.")
    await PostgresAuthorizationLeaseRepository(require_domain_pool()).revoke(
        actor_key=actor_key,
        lease_id=lease_id,
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store, private"})


@app.get("/v1/operations/readiness")
async def operational_readiness(request: Request) -> Response:
    _actor_key, permissions = _identity(request)
    _require_permission(permissions, "maintenance:run")
    try:
        async with require_domain_pool().connection() as connection:
            await connection.execute("SELECT 1")
    except Exception:
        return Response(
            content='{"status":"not_ready","database_ready":false}',
            status_code=503,
            media_type="application/json",
        )
    return Response(
        content='{"status":"ready","database_ready":true}',
        media_type="application/json",
    )


@app.get("/v1/lineage/{node_id}")
async def lineage_graph(
    request: Request,
    node_id: str,
    direction: str = Query(default="both", pattern="^(backward|forward|both)$"),
    depth: int = Query(default=4, ge=1, le=8),
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "quant:read")
    if not _SAFE_ID.fullmatch(node_id):
        raise HTTPException(status_code=422, detail="Invalid lineage node ID.")
    with database_actor_context(actor_key):
        graph = await PostgresLineageRepository(require_domain_pool()).graph(
            actor_key=actor_key,
            node_id=node_id,
            direction=direction,
            depth=depth,
            limit=limit,
        )
    if graph is None:
        raise HTTPException(status_code=404, detail="Lineage node was not found.")
    return graph


@app.get("/v1/experiments")
async def experiment_catalog(
    request: Request,
    response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    status: list[str] = Query(default=[]),
) -> dict:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "quant:read")
    unique_statuses = tuple(dict.fromkeys(status))
    if any(item not in EXPERIMENT_STATUSES for item in unique_statuses):
        raise HTTPException(status_code=422, detail="Invalid experiment status filter.")
    response.headers["Cache-Control"] = "no-store, private"
    try:
        with database_actor_context(actor_key):
            return await PostgresExperimentCatalogRepository(
                require_domain_pool()
            ).list_experiments(
                actor_key=actor_key,
                limit=limit,
                statuses=unique_statuses,
                cursor=cursor,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/experiments/{experiment_id}/dossier")
async def experiment_dossier(request: Request, experiment_id: str) -> dict:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "quant:read")
    if not re.fullmatch(r"exp_v1_[0-9a-f]{32}", experiment_id):
        raise HTTPException(status_code=422, detail="Invalid experiment ID.")
    with database_actor_context(actor_key):
        dossier = await PostgresLineageRepository(
            require_domain_pool()
        ).experiment_dossier(actor_key=actor_key, experiment_id=experiment_id)
    if dossier is None:
        raise HTTPException(status_code=404, detail="Experiment was not found.")
    return dossier


def _quant_client() -> QlibClient:
    return QlibClient(
        settings.quant_worker_url,
        timeout_seconds=settings.quant_worker_timeout_seconds,
    )


@app.get("/v1/experiments/compare")
async def compare_experiments(
    request: Request,
    experiment_ids: list[str] = Query(...),
) -> dict:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "quant:read")
    unique_ids = list(dict.fromkeys(experiment_ids))
    if not 2 <= len(unique_ids) <= 5 or any(
        not re.fullmatch(r"exp_v1_[0-9a-f]{32}", item) for item in unique_ids
    ):
        raise HTTPException(status_code=422, detail="Provide 2 to 5 valid experiment IDs.")
    with database_actor_context(actor_key):
        comparison = await PostgresLineageRepository(
            require_domain_pool()
        ).compare_experiments(actor_key=actor_key, experiment_ids=unique_ids)
    if comparison is None:
        raise HTTPException(status_code=404, detail="An experiment was not found.")
    return comparison


@app.get("/v1/experiments/{experiment_id}/export")
async def export_experiment_dossier(request: Request, experiment_id: str) -> Response:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "quant:read")
    if not re.fullmatch(r"exp_v1_[0-9a-f]{32}", experiment_id):
        raise HTTPException(status_code=422, detail="Invalid experiment ID.")
    with database_actor_context(actor_key):
        bundle = await PostgresLineageRepository(require_domain_pool()).export_dossier(
            actor_key=actor_key,
            experiment_id=experiment_id,
        )
    if bundle is None:
        raise HTTPException(status_code=404, detail="Experiment was not found.")
    return Response(
        content=json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{experiment_id}-dossier-v1.json"'
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Dossier-Content-Hash": bundle["content_hash"],
        },
    )


@app.get("/v1/artifacts/{artifact_id}/content")
async def artifact_content(request: Request, artifact_id: str) -> Response:
    actor_key, permissions = _identity(request)
    _require_permission(permissions, "quant:read")
    client = _quant_client()
    try:
        manifest = await client.get_artifact(artifact_id, actor_key=actor_key)
        if manifest.get("storage_status") != "ready":
            raise HTTPException(status_code=410, detail="Artifact is no longer available.")
        if not manifest.get("download_eligible"):
            raise HTTPException(status_code=403, detail="Artifact is internal-only.")
        content, media_type, verified_hash = await client.download_artifact(
            artifact_id,
            actor_key=actor_key,
            max_bytes=settings.nexus_artifact_download_max_bytes,
        )
    except HTTPException:
        raise
    except QuantWorkerError as exc:
        raise HTTPException(
            status_code=(
                404
                if exc.code in {"unknown_artifact", "invalid_artifact_id"}
                else 413
                if exc.code == "artifact_too_large"
                else 503
            ),
            detail=exc.message,
        ) from exc
    if verified_hash != manifest["content_hash"]:
        raise HTTPException(status_code=409, detail="Artifact verification failed.")
    extension = (
        ".parquet"
        if media_type.startswith("application/vnd.apache.parquet")
        else ""
    )
    filename = f"{manifest.get('artifact_type', 'artifact')}{extension}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "X-Artifact-Content-Hash": manifest["content_hash"],
        },
    )
