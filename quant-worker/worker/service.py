"""Runtime composition for the quant application and infrastructure adapters."""

import logging
from pathlib import Path
from typing import Any

from worker.adapters import (
    FilesystemArtifactObjectStore,
    FilesystemExperimentRepository,
    PostgresExperimentRepository,
    QlibRunner,
)
from worker.application import ExperimentApplicationService
from worker.observability import log_event
from worker.security import current_actor_key

logger = logging.getLogger(__name__)


def _emit_event(event: str, **fields: Any) -> None:
    log_event(logger, event, **fields)


class QlibExperimentWorker(ExperimentApplicationService):
    """Worker assembled from the application service and concrete adapters."""

    def __init__(
        self,
        data_dir: Path,
        artifacts_dir: Path,
        runner: Any | None = None,
        domain_database_url: str | None = None,
        global_active_job_limit: int = 4,
        queued_job_ttl_seconds: int = 86_400,
        max_execution_seconds: int = 3_600,
        actor_storage_limit_bytes: int = 5 * 1024 * 1024 * 1024,
        artifact_retention_seconds: int = 90 * 24 * 60 * 60,
        artifact_store: Any | None = None,
    ) -> None:
        filesystem = FilesystemExperimentRepository(
            data_dir,
            artifacts_dir,
            artifact_retention_seconds=artifact_retention_seconds,
            artifact_store=artifact_store or FilesystemArtifactObjectStore(),
        )
        repository = (
            PostgresExperimentRepository(
                filesystem,
                domain_database_url,
                current_actor_key,
                global_active_job_limit=global_active_job_limit,
                queued_job_ttl_seconds=queued_job_ttl_seconds,
                actor_storage_limit_bytes=actor_storage_limit_bytes,
            )
            if domain_database_url
            else filesystem
        )
        qlib_runner = QlibRunner(max_execution_seconds=max_execution_seconds)
        super().__init__(
            repository,
            runner or qlib_runner,
            actor_provider=current_actor_key,
            event_sink=_emit_event,
        )
