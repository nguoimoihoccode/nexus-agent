"""FastAPI entrypoint for the internal Qlib worker."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse

from worker.adapters import FilesystemArtifactObjectStore
from worker.models import (
    CancellationRequest,
    DatasetInfo,
    DatasetValidation,
    ExperimentAccepted,
    ExperimentRecord,
    ExperimentRequest,
)
from worker.observability import configure_logging, install_http_logging
from worker.domain import WorkerError
from worker.service import QlibExperimentWorker
from worker.security import install_worker_auth

configure_logging("quant-worker")


def nexus_data_dir(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    return Path(values.get("NEXUS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))


def runtime_paths(env: Mapping[str, str] | None = None) -> tuple[Path, Path]:
    values = os.environ if env is None else env
    root = nexus_data_dir(values)
    return (
        Path(values.get("QLIB_DATA_DIR", root / "qlib")),
        Path(values.get("QUANT_ARTIFACTS_DIR", root / "artifacts")),
    )


def positive_int_setting(
    values: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = values.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


def worker_http_error(exc: WorkerError) -> HTTPException:
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    if exc.code in {
        "unknown_dataset",
        "unknown_experiment",
        "invalid_experiment_id",
        "unknown_artifact",
        "invalid_artifact_id",
    }:
        status_code = status.HTTP_404_NOT_FOUND
    elif exc.code == "artifact_not_downloadable":
        status_code = status.HTTP_403_FORBIDDEN
    elif exc.code in {"artifact_expired", "artifact_deleted"}:
        status_code = status.HTTP_410_GONE
    elif exc.code in {
        "dataset_not_ready",
        "experiment_quota_exceeded",
        "global_experiment_quota_exceeded",
        "idempotency_conflict",
    }:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(status_code=status_code, detail=exc.as_detail())


def create_service(env: Mapping[str, str] | None = None) -> QlibExperimentWorker:
    """Compose the default worker from runtime configuration."""
    data_dir, artifacts_dir = runtime_paths(env)
    values = os.environ if env is None else env
    database_url = values.get("NEXUS_DOMAIN_DATABASE_URL", "").strip()
    environment = values.get("NEXUS_ENV", "development")
    if environment == "production" and not database_url:
        raise RuntimeError("NEXUS_DOMAIN_DATABASE_URL is required in production.")
    artifact_store = FilesystemArtifactObjectStore()
    return QlibExperimentWorker(
        data_dir,
        artifacts_dir,
        domain_database_url=database_url or None,
        global_active_job_limit=positive_int_setting(
            values,
            "QUANT_WORKER_GLOBAL_ACTIVE_JOB_LIMIT",
            4,
        ),
        queued_job_ttl_seconds=positive_int_setting(
            values,
            "QUANT_WORKER_QUEUED_JOB_TTL_SECONDS",
            86_400,
        ),
        max_execution_seconds=positive_int_setting(
            values,
            "QUANT_WORKER_MAX_EXECUTION_SECONDS",
            3_600,
        ),
        actor_storage_limit_bytes=positive_int_setting(
            values,
            "QUANT_WORKER_ACTOR_STORAGE_LIMIT_BYTES",
            5 * 1024 * 1024 * 1024,
        ),
        artifact_retention_seconds=positive_int_setting(
            values,
            "QUANT_WORKER_ARTIFACT_RETENTION_SECONDS",
            90 * 24 * 60 * 60,
        ),
        artifact_store=artifact_store,
    )


def create_app(
    worker: QlibExperimentWorker | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> FastAPI:
    """Create the HTTP adapter with an injectable application service."""
    service = worker
    owns_service = worker is None

    def get_service() -> QlibExperimentWorker:
        nonlocal service
        if service is None:
            service = create_service(env)
        return service

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        active_service = get_service()
        active_service.start_coordinator()
        try:
            yield
        finally:
            if owns_service:
                active_service.close(wait=True)

    app = FastAPI(title="Nexus Quant Worker", version="0.1.0", lifespan=lifespan)
    install_http_logging(app, service="quant-worker")
    install_worker_auth(app)

    @app.get("/health")
    def health() -> dict[str, str | bool]:
        return {"status": "ok", "worker": "quant-worker", "process_alive": True}

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
            {
                "status": "ready" if ready else "not_ready",
                "dependencies_ready": ready,
            },
            status_code=200 if ready else 503,
        )

    @app.get("/v1/datasets", response_model=list[DatasetInfo])
    def list_datasets() -> list[DatasetInfo]:
        return get_service().list_datasets()

    @app.post("/v1/datasets/{dataset_id}/validate", response_model=DatasetValidation)
    def validate_dataset(
        dataset_id: str,
        universe: str = Query(...),
    ) -> DatasetValidation:
        try:
            return get_service().validate_dataset(dataset_id, universe)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    @app.post(
        "/v1/experiments",
        response_model=ExperimentAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_experiment(request: ExperimentRequest) -> ExperimentAccepted:
        try:
            return get_service().submit_experiment(request)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    @app.get("/v1/experiments/{experiment_id}/result")
    def get_experiment_result(experiment_id: str) -> dict[str, Any]:
        try:
            return get_service().experiment_result(experiment_id)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    @app.post(
        "/v1/experiments/{experiment_id}/cancel",
        response_model=ExperimentRecord,
    )
    def cancel_experiment(
        experiment_id: str,
        request: CancellationRequest,
    ) -> ExperimentRecord:
        try:
            return get_service().cancel_experiment(
                experiment_id,
                reason_category=request.reason_category,
            )
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    @app.get("/v1/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str) -> dict[str, Any]:
        try:
            return get_service().artifact_manifest(artifact_id)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc

    @app.get("/v1/artifacts/{artifact_id}/content")
    def download_artifact(artifact_id: str):
        try:
            path, manifest = get_service().artifact_path(artifact_id)
        except WorkerError as exc:
            raise worker_http_error(exc) from exc
        return FileResponse(
            path,
            media_type=manifest["media_type"],
            filename=path.name,
            headers={"X-Artifact-Content-Hash": manifest["content_hash"]},
        )

    return app


app = create_app()
