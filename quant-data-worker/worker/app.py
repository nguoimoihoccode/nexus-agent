"""FastAPI entrypoint for the quant data worker."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from worker.models import (
    BuildQlibDatasetRequest,
    FactorSnapshotRequest,
    FactorSnapshotResponse,
    FetchOhlcvRequest,
    FetchOhlcvResponse,
    MarketDataValidation,
    QlibDatasetBuildResponse,
    QlibDatasetValidation,
)
from worker.observability import configure_logging, install_http_logging
from worker.adapters import MarketDataStore, OpenBBFetcher, PostgresQuantDataControlPlane
from worker.domain import WorkerError
from worker.service import OpenBBDataWorker
from worker.security import install_worker_auth

configure_logging("quant-data-worker")


def nexus_data_dir(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    return Path(values.get("NEXUS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))


def runtime_paths(env: Mapping[str, str] | None = None) -> tuple[Path, Path]:
    values = os.environ if env is None else env
    root = nexus_data_dir(values)
    return (
        Path(values.get("QUANT_DATA_WORKER_DATA_DIR", root / "qlib")),
        Path(values.get("QUANT_DATA_WORKER_STAGING_DIR", root / "staging")),
    )


def worker_http_error(exc: WorkerError) -> HTTPException:
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    if exc.code in {"unknown_staging_dataset", "unknown_dataset"}:
        status_code = status.HTTP_404_NOT_FOUND
    elif exc.code in {"dataset_exists", "idempotency_conflict"}:
        status_code = status.HTTP_409_CONFLICT
    elif exc.code == "openbb_unavailable":
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(status_code=status_code, detail=exc.as_detail())


def create_service(env: Mapping[str, str] | None = None) -> OpenBBDataWorker:
    """Compose the default data worker from runtime configuration."""
    data_dir, staging_dir = runtime_paths(env)
    values = os.environ if env is None else env
    database_url = values.get("NEXUS_DOMAIN_DATABASE_URL", "").strip()
    environment = values.get("NEXUS_ENV", "development")
    if environment == "production" and not database_url:
        raise RuntimeError("NEXUS_DOMAIN_DATABASE_URL is required in production.")
    control_plane = (
        PostgresQuantDataControlPlane(database_url) if database_url else None
    )
    return OpenBBDataWorker(
        MarketDataStore(staging_dir, data_dir),
        OpenBBFetcher.preload(),
        control_plane,
    )


def create_app(
    worker: OpenBBDataWorker | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> FastAPI:
    """Create the HTTP adapter with an injectable application service."""
    service = worker
    owns_service = worker is None

    def get_service() -> OpenBBDataWorker:
        nonlocal service
        if service is None:
            service = create_service(env)
        return service

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        active_service = get_service()
        try:
            yield
        finally:
            if owns_service:
                active_service.close()

    app = FastAPI(
        title="Nexus Quant Data Worker",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_http_logging(app, service="quant-data-worker")
    install_worker_auth(app)

    @app.get("/health")
    def health() -> dict[str, str | bool]:
        return {
            "status": "ok",
            "worker": "quant-data-worker",
            "process_alive": True,
        }

    @app.get("/ready")
    def readiness():
        try:
            ready = get_service().readiness()
        except Exception:
            return JSONResponse(
                {"status": "not_ready", "dependencies_ready": False},
                status_code=503,
            )
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "dependencies_ready": ready},
            status_code=200 if ready else 503,
        )

    @app.post("/v1/openbb/ohlcv", response_model=FetchOhlcvResponse)
    def fetch_openbb_ohlcv(request: FetchOhlcvRequest) -> FetchOhlcvResponse:
        try:
            return get_service().fetch_openbb_ohlcv(request)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    @app.post("/v1/factors/snapshot", response_model=FactorSnapshotResponse)
    def fetch_factor_snapshot(request: FactorSnapshotRequest) -> FactorSnapshotResponse:
        try:
            return get_service().fetch_factor_snapshot(request)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    @app.post(
        "/v1/staging/{dataset_staging_id}/validate",
        response_model=MarketDataValidation,
    )
    def validate_market_data(dataset_staging_id: str) -> MarketDataValidation:
        try:
            return get_service().validate_market_data(dataset_staging_id)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    @app.post("/v1/qlib-datasets", response_model=QlibDatasetBuildResponse)
    def build_qlib_dataset(
        request: BuildQlibDatasetRequest,
    ) -> QlibDatasetBuildResponse:
        try:
            return get_service().build_qlib_dataset(request)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    @app.post(
        "/v1/qlib-datasets/{dataset_id}/validate",
        response_model=QlibDatasetValidation,
    )
    def validate_qlib_dataset(dataset_id: str) -> QlibDatasetValidation:
        try:
            return get_service().validate_qlib_dataset(dataset_id)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    return app


app = create_app()
