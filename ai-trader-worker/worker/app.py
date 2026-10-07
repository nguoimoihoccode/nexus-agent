"""FastAPI entrypoint for the Nexus AI-Trader adapter worker."""

from __future__ import annotations

import logging
import os
import secrets
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse

from worker.adapters import PublicationConflict
from worker.models import DiscussionRequest, MarketRefreshRequest, StrategyRequest
from worker.observability import (
    configure_logging,
    install_http_logging,
    log_event,
)
from worker.runtime import runtime_db_path
from worker.service import AiTraderWorker, WorkerConfigError

configure_logging("ai-trader-worker")
logger = logging.getLogger(__name__)


def default_token_provider() -> str | None:
    return os.environ.get("AI_TRADER_TOKEN")


def create_app(
    worker: AiTraderWorker | None = None,
    *,
    token_provider: Callable[[], str | None] = default_token_provider,
) -> FastAPI:
    service = worker
    app = FastAPI(title="Nexus AI-Trader Worker", version="0.1.0")
    install_http_logging(app, service="ai-trader-worker")

    def get_service() -> AiTraderWorker:
        nonlocal service
        if service is None:
            service = AiTraderWorker(runtime_db_path())
        return service

    def require_token(
        authorization: str | None = Header(default=None),
        x_claw_token: str | None = Header(default=None, alias="X-Claw-Token"),
    ) -> None:
        expected = (token_provider() or "").strip()
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI_TRADER_TOKEN is not configured.",
            )
        supplied = _extract_token(authorization, x_claw_token)
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
            )

    @app.get("/health")
    def health() -> dict[str, str | bool]:
        return {
            "status": "ok",
            "worker": "ai-trader-worker",
            "process_alive": True,
        }

    @app.get("/ready")
    def readiness(_: None = Depends(require_token)):
        try:
            result = get_service().health()
        except Exception:
            return JSONResponse(
                {"status": "not_ready", "dependencies_ready": False},
                status_code=503,
            )
        ready = result.get("status") == "ok"
        return JSONResponse(
            {
                **result,
                "status": "ready" if ready else "not_ready",
                "dependencies_ready": ready,
            },
            status_code=200 if ready else 503,
        )

    @app.get("/api/market-intel/overview")
    def market_intel_overview() -> dict[str, object]:
        return get_service().market_overview()

    @app.get("/api/market-intel/news")
    def market_intel_news(
        category: str | None = None,
        limit: int = Query(default=5, ge=1, le=12),
        symbols: str | None = Query(default=None),
    ) -> dict[str, object]:
        return get_service().market_news(category=category, limit=limit, symbols=symbols)

    @app.post("/api/market-intel/refresh")
    def refresh_market_intel(
        request: MarketRefreshRequest | None = None,
        _: None = Depends(require_token),
    ) -> dict[str, object]:
        try:
            return get_service().refresh_market_intel(request or MarketRefreshRequest())
        except WorkerConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.message,
            ) from exc

    @app.get("/api/signals/feed")
    def signal_feed(
        message_type: str | None = None,
        market: str | None = None,
        keyword: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        sort: str = "new",
    ) -> dict[str, object]:
        return get_service().signal_feed(
            message_type=message_type,
            market=market,
            keyword=keyword,
            limit=limit,
            offset=offset,
            sort=sort,
        )

    @app.post("/api/signals/strategy")
    def publish_strategy(
        request: StrategyRequest,
        _: None = Depends(require_token),
    ) -> dict[str, object]:
        try:
            result = get_service().publish_strategy(request)
        except PublicationConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Publication idempotency key conflicts with another action.",
            ) from exc
        log_event(
            logger,
            "audit.signal.published",
            signal_id=result.get("signal_id"),
            message_type="strategy",
            market=request.market,
        )
        return result

    @app.post("/api/signals/discussion")
    def publish_discussion(
        request: DiscussionRequest,
        _: None = Depends(require_token),
    ) -> dict[str, object]:
        try:
            result = get_service().publish_discussion(request)
        except PublicationConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Publication idempotency key conflicts with another action.",
            ) from exc
        log_event(
            logger,
            "audit.signal.published",
            signal_id=result.get("signal_id"),
            message_type="discussion",
            market=request.market,
        )
        return result

    @app.post("/api/claw/agents/heartbeat")
    def heartbeat(_: None = Depends(require_token)) -> dict[str, object]:
        return get_service().heartbeat()

    return app


def _extract_token(authorization: str | None, x_claw_token: str | None) -> str | None:
    if x_claw_token and x_claw_token.strip():
        return x_claw_token.strip()
    if not authorization:
        return None
    value = authorization.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value or None


app = create_app()
