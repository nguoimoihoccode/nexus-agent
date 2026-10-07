"""PostgreSQL experiment metadata with filesystem dataset/artifact bytes."""

from __future__ import annotations

import hashlib
from typing import Any, Callable
from uuid import uuid4

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from worker.adapters.filesystem_repository import FilesystemExperimentRepository
from worker.domain import (
    DatasetInspection,
    WorkerError,
    content_hash,
    new_experiment_id,
)
from worker.models import (
    DatasetInfo,
    DatasetValidation,
    ExperimentRecord,
    JobStatus,
    WorkerError as WorkerErrorModel,
)
from worker.security import actor_context, bind_database_actor, reset_database_actor

DOMAIN_SCHEMA_VERSION = 14


class PostgresExperimentRepository:
    """Use PostgreSQL as record authority while retaining storage ports for bytes."""

    def __init__(
        self,
        filesystem: FilesystemExperimentRepository,
        conninfo: str,
        actor_provider: Callable[[], str],
        *,
        global_active_job_limit: int = 4,
        queued_job_ttl_seconds: int = 86_400,
        actor_storage_limit_bytes: int = 5 * 1024 * 1024 * 1024,
    ) -> None:
        self.filesystem = filesystem
        self.data_dir = filesystem.data_dir
        self.artifacts_dir = filesystem.artifacts_dir
        self._actor_provider = actor_provider
        if global_active_job_limit < 1:
            raise ValueError("global_active_job_limit must be positive")
        self._global_active_job_limit = global_active_job_limit
        if queued_job_ttl_seconds < 1:
            raise ValueError("queued_job_ttl_seconds must be positive")
        self._queued_job_ttl_seconds = queued_job_ttl_seconds
        if actor_storage_limit_bytes < 1:
            raise ValueError("actor_storage_limit_bytes must be positive")
        self._actor_storage_limit_bytes = actor_storage_limit_bytes
        self.pool = ConnectionPool(
            conninfo,
            min_size=1,
            max_size=8,
            kwargs={"prepare_threshold": 0},
            name="quant-experiment-control-plane",
            open=True,
            check=bind_database_actor,
            reset=reset_database_actor,
        )
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT version FROM nexus_domain.schema_migrations "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
        if row is None or row[0] != DOMAIN_SCHEMA_VERSION:
            self.pool.close()
            raise RuntimeError(
                "Domain schema version is incompatible with quant-worker."
            )

    def readiness(self) -> bool:
        if not self.filesystem.readiness():
            return False
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT version FROM nexus_domain.schema_migrations "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
        return bool(row and row[0] == DOMAIN_SCHEMA_VERSION)

    def recover_interrupted(self) -> int:
        error = WorkerErrorModel(
            code="worker_interrupted",
            message="The worker restarted before the experiment completed.",
            retryable=True,
        )
        expired_error = WorkerErrorModel(
            code="queued_job_expired",
            message="The experiment expired before a worker lease was acquired.",
            stage="queue",
            retryable=False,
        )
        with self.pool.connection() as connection:
            actors = [
                row[0]
                for row in connection.execute(
                    "SELECT actor_key FROM nexus_domain.quant_recovery_actors(%s)",
                    (self._queued_job_ttl_seconds,),
                ).fetchall()
            ]
        recovered = 0
        for actor in actors:
            with actor_context(actor):
                with self.pool.connection() as connection, connection.transaction():
                    expired_rows = connection.execute(
                        """
                        SELECT experiment_id
                        FROM nexus_domain.experiments
                        WHERE actor_key = %s AND status = 'queued'
                          AND created_at < now() - make_interval(secs => %s)
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        """,
                        (actor, self._queued_job_ttl_seconds),
                    ).fetchall()
                    for (experiment_id,) in expired_rows:
                        connection.execute(
                            """
                            UPDATE nexus_domain.experiments
                            SET status = 'expired', error = %s,
                                progress_stage = 'queue', updated_at = now()
                            WHERE actor_key = %s AND experiment_id = %s
                              AND status = 'queued'
                            """,
                            (
                                Jsonb(expired_error.model_dump(mode="json")),
                                actor,
                                experiment_id,
                            ),
                        )
                        self._event(
                            connection,
                            actor,
                            experiment_id,
                            "worker.expired",
                            _worker_payload(experiment_id, "expired", stage="queue"),
                        )
                    rows = connection.execute(
                        """
                        SELECT e.experiment_id, e.status, e.attempt_limit,
                               (
                                   SELECT count(*)
                                   FROM nexus_domain.job_attempts a
                                   WHERE a.actor_key = e.actor_key
                                     AND a.experiment_id = e.experiment_id
                               ),
                               e.cancellation
                        FROM nexus_domain.experiments e
                        WHERE e.actor_key = %s
                          AND e.status IN ('leased', 'running', 'cancelling')
                          AND (
                              e.lease_expires_at IS NULL
                              OR e.lease_expires_at < now()
                          )
                        ORDER BY e.created_at
                        FOR UPDATE OF e SKIP LOCKED
                        """,
                        (actor,),
                    ).fetchall()
                    recovery_time = connection.execute("SELECT now()").fetchone()[0]
                    for (
                        experiment_id,
                        status,
                        attempt_limit,
                        attempts,
                        cancellation,
                    ) in rows:
                        cancelled = status == JobStatus.CANCELLING.value
                        retry = not cancelled and attempts < attempt_limit
                        next_status = (
                            JobStatus.CANCELLED.value
                            if cancelled
                            else JobStatus.QUEUED.value
                            if retry
                            else JobStatus.FAILED.value
                        )
                        terminal_error = (
                            None if cancelled or retry else error.model_dump(mode="json")
                        )
                        cancellation_value = cancellation
                        if cancelled:
                            cancellation_value = {
                                **(cancellation or {}),
                                "completed_at": recovery_time.isoformat(),
                                "resulting_state": JobStatus.CANCELLED.value,
                            }
                        connection.execute(
                            """
                            UPDATE nexus_domain.experiments
                            SET status = %s, error = %s, cancellation = %s,
                                lease_owner = NULL, lease_expires_at = NULL,
                                heartbeat_at = NULL, updated_at = now()
                            WHERE actor_key = %s AND experiment_id = %s
                            """,
                            (
                                next_status,
                                Jsonb(terminal_error)
                                if terminal_error is not None
                                else None,
                                Jsonb(cancellation_value)
                                if cancellation_value is not None
                                else None,
                                actor,
                                experiment_id,
                            ),
                        )
                        attempt_status = (
                            JobStatus.CANCELLED.value if cancelled else "interrupted"
                        )
                        connection.execute(
                            """
                            UPDATE nexus_domain.job_attempts
                            SET status = %s, stage = 'recovery', finished_at = now(),
                                safe_error = %s
                            WHERE actor_key = %s AND experiment_id = %s
                              AND attempt = (
                                  SELECT MAX(attempt)
                                  FROM nexus_domain.job_attempts
                                  WHERE actor_key = %s AND experiment_id = %s
                              )
                              AND finished_at IS NULL
                            """,
                            (
                                attempt_status,
                                Jsonb(error.model_dump(mode="json"))
                                if not cancelled
                                else None,
                                actor,
                                experiment_id,
                                actor,
                                experiment_id,
                            ),
                        )
                        self._event(
                            connection,
                            actor,
                            experiment_id,
                            "worker.cancelled"
                            if cancelled
                            else "worker.requeued"
                            if retry
                            else "worker.failed",
                            _worker_payload(
                                experiment_id,
                                "cancelled"
                                if cancelled
                                else "requeued"
                                if retry
                                else "failed",
                                stage="recovery",
                                recovery=True,
                            ),
                        )
                    recovered += len(expired_rows) + len(rows)
        return recovered

    def expire_artifacts(self, *, limit: int = 100) -> int:
        """Tombstone expired artifacts atomically, then remove inaccessible bytes."""
        expired: list[tuple[str, str]] = []
        with self.pool.connection() as connection:
            candidates = connection.execute(
                "SELECT actor_key, artifact_id "
                "FROM nexus_domain.quant_expired_artifacts(%s)",
                (limit,),
            ).fetchall()
        artifacts_by_actor: dict[str, list[str]] = {}
        for actor, artifact_id in candidates:
            artifacts_by_actor.setdefault(actor, []).append(artifact_id)
        for actor, artifact_ids in artifacts_by_actor.items():
            with actor_context(actor):
                with self.pool.connection() as connection, connection.transaction():
                    rows = connection.execute(
                        """
                        SELECT artifact_id, content_hash, storage_key, manifest
                        FROM nexus_domain.artifacts
                        WHERE actor_key = %s AND artifact_id = ANY(%s)
                          AND deleted_at IS NULL AND expires_at IS NOT NULL
                          AND expires_at <= now()
                        ORDER BY expires_at, artifact_id
                        FOR UPDATE SKIP LOCKED
                        """,
                        (actor, artifact_ids),
                    ).fetchall()
                    deleted_at = connection.execute("SELECT now()").fetchone()[0]
                    for artifact_id, digest, storage_key, manifest in rows:
                        connection.execute(
                            """
                            UPDATE nexus_domain.artifacts
                            SET deleted_at = %s
                            WHERE actor_key = %s AND artifact_id = %s
                              AND deleted_at IS NULL
                            """,
                            (deleted_at, actor, artifact_id),
                        )
                        connection.execute(
                            """
                            INSERT INTO nexus_domain.artifact_tombstones
                                (actor_key, artifact_id, deleted_at, reason,
                                 prior_content_hash)
                            VALUES (%s, %s, %s, 'retention_expired', %s)
                            ON CONFLICT (actor_key, artifact_id) DO NOTHING
                            """,
                            (actor, artifact_id, deleted_at, digest),
                        )
                        producer_type = str(
                            manifest.get("producer_type") or "qlib_experiment"
                        )
                        producer_id = str(manifest.get("producer_id") or artifact_id)
                        lifecycle = {
                            "artifact_id": artifact_id,
                            "producer_type": producer_type,
                            "producer_id": producer_id,
                            "content_hash": digest,
                            "reason": "retention_expired",
                        }
                        self._event(
                            connection,
                            actor,
                            producer_id,
                            "artifact.expired",
                            {**lifecycle, "status": "expired"},
                        )
                        self._event(
                            connection,
                            actor,
                            producer_id,
                            "artifact.deleted",
                            {**lifecycle, "status": "deleted"},
                        )
                        expired.append((actor, storage_key))

        # Also retry cleanup for previously committed tombstones. Database state
        # already denies reads, so a cleanup failure cannot re-expose the object.
        with self.pool.connection() as connection:
            cleanup = connection.execute(
                "SELECT actor_key, storage_key "
                "FROM nexus_domain.quant_deleted_artifacts(%s)",
                (max(limit, 500),),
            ).fetchall()
        for actor, storage_key in cleanup:
            with actor_context(actor):
                self.filesystem.delete_artifact_bytes(storage_key)
        return len(expired)

    def pending_jobs(self) -> list[tuple[str, str]]:
        with self.pool.connection() as connection:
            return [
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT actor_key, experiment_id "
                    "FROM nexus_domain.quant_pending_experiments()"
                ).fetchall()
            ]

    def reserve_experiment(
        self,
        request,
        *,
        idempotency_key: str,
        arguments_digest: str,
    ) -> tuple[ExperimentRecord, bool]:
        actor = self._actor_provider()
        specification = request.model_dump(mode="json")
        specification_digest = _specification_digest(request)
        experiment_id = new_experiment_id()
        from worker.models import utc_now

        now = utc_now()
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("nexus:experiments:global-admission",),
            )
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (actor,),
            )
            existing = connection.execute(
                """
                SELECT experiment_id, arguments_digest, status, created_at,
                       updated_at, specification, result, error
                FROM nexus_domain.experiments
                WHERE actor_key = %s AND idempotency_key = %s
                FOR UPDATE
                """,
                (actor, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing[1] != arguments_digest:
                    raise WorkerError(
                        "idempotency_conflict",
                        "Idempotency key was reused with different arguments.",
                        stage="idempotency",
                    )
                return (
                    ExperimentRecord(
                        experiment_id=existing[0],
                        status=existing[2],
                        created_at=existing[3],
                        updated_at=existing[4],
                        request=existing[5],
                        result=existing[6],
                        error=existing[7],
                    ),
                    False,
                )
            active = connection.execute(
                """
                SELECT count(*) FROM nexus_domain.experiments
                WHERE actor_key = %s
                  AND status IN ('queued', 'leased', 'running', 'cancelling')
                """,
                (actor,),
            ).fetchone()[0]
            if active:
                raise WorkerError(
                    "experiment_quota_exceeded",
                    "Only one active experiment is allowed per actor.",
                    stage="quota",
                )
            global_active = connection.execute(
                "SELECT nexus_domain.quant_active_experiment_count()"
            ).fetchone()[0]
            if global_active >= self._global_active_job_limit:
                raise WorkerError(
                    "global_experiment_quota_exceeded",
                    "The global active experiment limit has been reached.",
                    stage="quota",
                    retryable=True,
                )
            connection.execute(
                """
                INSERT INTO nexus_domain.experiments
                    (actor_key, experiment_id, dataset_revision_id,
                     specification_digest, specification, status,
                     idempotency_key, arguments_digest, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s, %s, %s)
                """,
                (
                    actor,
                    experiment_id,
                    request.dataset_id,
                    specification_digest,
                    Jsonb(specification),
                    idempotency_key,
                    arguments_digest,
                    now,
                    now,
                ),
            )
            dataset = connection.execute(
                """
                SELECT manifest_hash, limitations
                FROM nexus_domain.dataset_revisions
                WHERE actor_key = %s AND dataset_revision_id = %s
                  AND status = 'ready'
                """,
                (actor, request.dataset_id),
            ).fetchone()
            if dataset is None:
                raise WorkerError(
                    "unknown_dataset",
                    "The dataset revision is not registered for this actor.",
                    stage="validation",
                )
            self._lineage_node(
                connection,
                actor,
                request.dataset_id,
                "dataset_revision",
                {
                    "dataset_revision_id": request.dataset_id,
                    "manifest_hash": dataset[0],
                    "limitations": dataset[1],
                },
            )
            self._lineage_node(
                connection,
                actor,
                experiment_id,
                "experiment",
                {
                    "experiment_id": experiment_id,
                    "dataset_revision_id": request.dataset_id,
                    "specification_digest": specification_digest,
                    "specification": specification,
                    "status": "queued",
                },
            )
            self._lineage_edge(
                connection,
                actor,
                "used_by_experiment",
                request.dataset_id,
                experiment_id,
            )
            record = ExperimentRecord(
                experiment_id=experiment_id,
                status=JobStatus.QUEUED,
                created_at=now,
                updated_at=now,
                request=request,
            )
            self._event(
                connection,
                actor,
                experiment_id,
                "worker.accepted",
                _worker_payload(experiment_id, "queued", stage="admission"),
            )
            return record, True

    def claim_experiment(
        self,
        actor: str,
        experiment_id: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> ExperimentRecord | None:
        if self._actor_provider() != actor:
            raise WorkerError("unknown_experiment", "Unknown experiment.")
        with self.pool.connection() as connection, connection.transaction():
            row = connection.execute(
                """
                UPDATE nexus_domain.experiments
                SET status = 'leased', lease_owner = %s,
                    lease_expires_at = now() + make_interval(secs => %s),
                    heartbeat_at = now(), updated_at = now()
                WHERE actor_key = %s AND experiment_id = %s AND status = 'queued'
                RETURNING created_at, updated_at, specification, result, error,
                          specification_digest, dataset_revision_id
                """,
                (lease_owner, lease_seconds, actor, experiment_id),
            ).fetchone()
            if row is None:
                return None
            attempt = connection.execute(
                """
                SELECT COALESCE(MAX(attempt), 0) + 1
                FROM nexus_domain.job_attempts
                WHERE actor_key = %s AND experiment_id = %s
                """,
                (actor, experiment_id),
            ).fetchone()[0]
            dataset = connection.execute(
                """
                SELECT manifest_hash, limitations
                FROM nexus_domain.dataset_revisions
                WHERE actor_key = %s AND dataset_revision_id = %s
                """,
                (actor, row[6]),
            ).fetchone()
            if dataset is not None:
                self._lineage_node(
                    connection,
                    actor,
                    row[6],
                    "dataset_revision",
                    {
                        "dataset_revision_id": row[6],
                        "manifest_hash": dataset[0],
                        "limitations": dataset[1],
                    },
                )
            self._lineage_node(
                connection,
                actor,
                experiment_id,
                "experiment",
                {
                    "experiment_id": experiment_id,
                    "dataset_revision_id": row[6],
                    "specification_digest": row[5],
                    "specification": row[2],
                    "status": "leased",
                },
            )
            if dataset is not None:
                self._lineage_edge(
                    connection,
                    actor,
                    "used_by_experiment",
                    row[6],
                    experiment_id,
                )
            connection.execute(
                """
                INSERT INTO nexus_domain.job_attempts
                    (actor_key, experiment_id, attempt, lease_owner,
                     started_at, status, stage)
                VALUES (%s, %s, %s, %s, now(), 'leased', 'claim')
                """,
                (actor, experiment_id, attempt, lease_owner),
            )
            attempt_id = f"{experiment_id}:attempt:{attempt}"
            self._lineage_node(
                connection,
                actor,
                attempt_id,
                "experiment_attempt",
                {
                    "experiment_id": experiment_id,
                    "attempt": int(attempt),
                    "lease_owner": lease_owner,
                    "status": "leased",
                },
            )
            self._lineage_edge(
                connection,
                actor,
                "executed_as_attempt",
                experiment_id,
                attempt_id,
            )
            self._event(
                connection,
                actor,
                experiment_id,
                "worker.leased",
                _worker_payload(experiment_id, "leased", stage="claim"),
            )
        return ExperimentRecord(
            experiment_id=experiment_id,
            status=JobStatus.LEASED,
            created_at=row[0],
            updated_at=row[1],
            request=row[2],
            result=row[3],
            error=row[4],
        )

    def heartbeat(
        self,
        actor: str,
        experiment_id: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        with self.pool.connection() as connection, connection.transaction():
            cursor = connection.execute(
                """
                UPDATE nexus_domain.experiments
                SET heartbeat_at = now(),
                    lease_expires_at = now() + make_interval(secs => %s),
                    updated_at = now()
                WHERE actor_key = %s AND experiment_id = %s
                  AND lease_owner = %s AND status IN ('leased', 'running')
                """,
                (lease_seconds, actor, experiment_id, lease_owner),
            )
            return cursor.rowcount == 1

    def requeue_for_shutdown(
        self,
        actor: str,
        experiment_id: str,
        lease_owner: str,
    ) -> None:
        """Release one live lease after its isolated execution has stopped."""
        with self.pool.connection() as connection, connection.transaction():
            cursor = connection.execute(
                """
                UPDATE nexus_domain.experiments
                SET status = 'queued', lease_owner = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    progress_stage = 'shutdown_recovery', updated_at = now()
                WHERE actor_key = %s AND experiment_id = %s
                  AND lease_owner = %s AND status IN ('leased', 'running')
                """,
                (actor, experiment_id, lease_owner),
            )
            if cursor.rowcount == 0:
                return
            connection.execute(
                """
                UPDATE nexus_domain.job_attempts
                SET status = 'interrupted', stage = 'shutdown_recovery',
                    finished_at = now(), safe_error = %s
                WHERE actor_key = %s AND experiment_id = %s
                  AND lease_owner = %s AND finished_at IS NULL
                """,
                (
                    Jsonb(
                        {
                            "code": "worker_shutdown",
                            "message": "Worker shutdown released the durable lease.",
                            "stage": "shutdown_recovery",
                            "retryable": True,
                        }
                    ),
                    actor,
                    experiment_id,
                    lease_owner,
                ),
            )
            self._event(
                connection,
                actor,
                experiment_id,
                "worker.requeued",
                _worker_payload(
                    experiment_id,
                    "requeued",
                    stage="shutdown_recovery",
                    recovery=True,
                ),
            )

    def record_runtime_versions(
        self,
        actor: str,
        experiment_id: str,
        lease_owner: str,
        versions: dict[str, str],
    ) -> None:
        with self.pool.connection() as connection, connection.transaction():
            cursor = connection.execute(
                """
                UPDATE nexus_domain.job_attempts
                SET runtime_versions = %s, stage = 'execution'
                WHERE actor_key = %s AND experiment_id = %s
                  AND lease_owner = %s
                  AND attempt = (
                      SELECT MAX(attempt) FROM nexus_domain.job_attempts
                      WHERE actor_key = %s AND experiment_id = %s
                  )
                """,
                (
                    Jsonb(versions),
                    actor,
                    experiment_id,
                    lease_owner,
                    actor,
                    experiment_id,
                ),
            )
            if cursor.rowcount != 1:
                raise WorkerError(
                    "attempt_persistence_failed",
                    "Experiment runtime versions could not be persisted.",
                    stage="persistence",
                    retryable=True,
                )

    def list_datasets(self) -> list[DatasetInfo]:
        actor = self._actor_provider()
        with self.pool.connection() as connection:
            allowed = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT dataset_revision_id
                    FROM nexus_domain.dataset_revisions
                    WHERE actor_key = %s AND status = 'ready'
                    """,
                    (actor,),
                ).fetchall()
            }
        return [
            item.model_copy(
                update={
                    "limitations": (limitations := self.dataset_limitations(item.dataset_id)),
                    "production_eligibility": limitations.get(
                        "production_eligibility", "blocked"
                    ),
                }
            )
            for item in self.filesystem.list_datasets()
            if item.dataset_id in allowed and item.manifest_verified
        ]

    def validate_dataset(self, dataset_id: str, universe: str) -> DatasetValidation:
        self._require_registered_dataset(dataset_id)
        validation = self.filesystem.validate_dataset(dataset_id, universe)
        limitations = self.dataset_limitations(dataset_id)
        return validation.model_copy(
            update={
                "limitations": limitations,
                "production_eligibility": limitations.get(
                    "production_eligibility", "blocked"
                ),
            }
        )

    def inspect_dataset(self, dataset_id: str, universe: str) -> DatasetInspection:
        self._require_registered_dataset(dataset_id)
        return self.filesystem.inspect_dataset(dataset_id, universe)

    def _require_registered_dataset(self, dataset_id: str) -> None:
        self.filesystem.require_dataset_id(dataset_id)
        actor = self._actor_provider()
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM nexus_domain.dataset_revisions
                WHERE actor_key = %s AND dataset_revision_id = %s AND status = 'ready'
                """,
                (actor, dataset_id),
            ).fetchone()
        if row is None:
            from worker.domain import WorkerError

            raise WorkerError("unknown_dataset", f"Unknown dataset: {dataset_id}")

    def dataset_limitations(self, dataset_id: str) -> dict[str, Any]:
        actor = self._actor_provider()
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT limitations FROM nexus_domain.dataset_revisions
                WHERE actor_key = %s AND dataset_revision_id = %s
                  AND status = 'ready'
                """,
                (actor, dataset_id),
            ).fetchone()
        if row is None:
            raise WorkerError("unknown_dataset", f"Unknown dataset: {dataset_id}")
        return dict(row[0] or {"production_eligibility": "blocked"})

    def load_record(self, experiment_id: str) -> ExperimentRecord | None:
        self.filesystem.experiment_dir(experiment_id)
        actor = self._actor_provider()
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT experiment_id, status, created_at, updated_at,
                       specification, result, error, progress_stage, cancellation
                FROM nexus_domain.experiments
                WHERE actor_key = %s AND experiment_id = %s
                """,
                (actor, experiment_id),
            ).fetchone()
        if row is None:
            return None
        return ExperimentRecord(
            experiment_id=row[0],
            status=row[1],
            created_at=row[2],
            updated_at=row[3],
            request=row[4],
            result=row[5],
            error=row[6],
            progress_stage=row[7],
            cancellation=row[8],
        )

    def request_cancellation(
        self,
        experiment_id: str,
        *,
        reason_category: str,
    ) -> ExperimentRecord:
        self.filesystem.experiment_dir(experiment_id)
        actor = self._actor_provider()
        from worker.models import utc_now

        requested_at = utc_now()
        with self.pool.connection() as connection, connection.transaction():
            row = connection.execute(
                """
                SELECT status, created_at, updated_at, specification, result, error,
                       progress_stage, cancellation
                FROM nexus_domain.experiments
                WHERE actor_key = %s AND experiment_id = %s
                FOR UPDATE
                """,
                (actor, experiment_id),
            ).fetchone()
            if row is None:
                raise WorkerError("unknown_experiment", "Unknown experiment.")
            current = JobStatus(row[0])
            if current in {
                JobStatus.CANCELLED,
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.EXPIRED,
            }:
                return ExperimentRecord(
                    experiment_id=experiment_id,
                    status=current,
                    created_at=row[1],
                    updated_at=row[2],
                    request=row[3],
                    result=row[4],
                    error=row[5],
                    progress_stage=row[6],
                    cancellation=row[7],
                )
            next_status = (
                JobStatus.CANCELLED
                if current == JobStatus.QUEUED
                else JobStatus.CANCELLING
            )
            cancellation = {
                "reason_category": reason_category,
                "requested_at": requested_at.isoformat(),
                "requested_by_actor": actor,
                **(
                    {
                        "completed_at": requested_at.isoformat(),
                        "resulting_state": JobStatus.CANCELLED.value,
                    }
                    if next_status == JobStatus.CANCELLED
                    else {}
                ),
            }
            updated = connection.execute(
                """
                UPDATE nexus_domain.experiments
                SET status = %s, cancellation = %s, updated_at = %s,
                    lease_owner = CASE WHEN %s THEN NULL ELSE lease_owner END,
                    lease_expires_at = CASE WHEN %s THEN NULL ELSE lease_expires_at END
                WHERE actor_key = %s AND experiment_id = %s AND status = %s
                RETURNING created_at, updated_at, specification, result, error,
                          progress_stage, cancellation
                """,
                (
                    next_status.value,
                    Jsonb(cancellation),
                    requested_at,
                    next_status == JobStatus.CANCELLED,
                    next_status == JobStatus.CANCELLED,
                    actor,
                    experiment_id,
                    current.value,
                ),
            ).fetchone()
            if updated is None:
                raise WorkerError(
                    "cancellation_conflict",
                    "Experiment state changed while cancellation was requested.",
                    stage="cancellation",
                    retryable=True,
                )
            self._event(
                connection,
                actor,
                experiment_id,
                "worker.cancelled"
                if next_status == JobStatus.CANCELLED
                else "worker.cancellation_requested",
                _worker_payload(
                    experiment_id,
                    next_status.value,
                    stage="cancellation",
                    reason_category=reason_category,
                ),
            )
        return ExperimentRecord(
            experiment_id=experiment_id,
            status=next_status,
            created_at=updated[0],
            updated_at=updated[1],
            request=updated[2],
            result=updated[3],
            error=updated[4],
            progress_stage=updated[5],
            cancellation=updated[6],
        )

    def save_record(self, record: ExperimentRecord) -> None:
        actor = self._actor_provider()
        digest = content_hash(
            "experiment_specification",
            record.request.model_dump(mode="json"),
        )
        idempotency_key = record.request.idempotency_key or f"legacy:{record.experiment_id}"
        arguments_digest = record.request.arguments_digest or digest
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                """
                INSERT INTO nexus_domain.experiments
                    (actor_key, experiment_id, dataset_revision_id,
                     specification_digest, specification, status,
                     result, error, created_at, updated_at,
                     idempotency_key, arguments_digest)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    actor,
                    record.experiment_id,
                    record.request.dataset_id,
                    digest,
                    Jsonb(record.request.model_dump(mode="json")),
                    record.status.value,
                    Jsonb(record.result) if record.result is not None else None,
                    Jsonb(record.error.model_dump(mode="json"))
                    if record.error is not None
                    else None,
                    record.created_at,
                    record.updated_at,
                    idempotency_key,
                    arguments_digest,
                ),
            )
            self._event(
                connection,
                actor,
                record.experiment_id,
                "worker.accepted",
                _worker_payload(record.experiment_id, "queued", stage="admission"),
            )

    def update_record(self, experiment_id: str, **changes: Any) -> ExperimentRecord:
        existing = self.load_record(experiment_id)
        if existing is None:
            raise KeyError(experiment_id)
        from worker.models import utc_now

        lease_owner = changes.pop("lease_owner", None)
        artifact_manifests: list[dict[str, Any]] = []
        if (
            changes.get("status") == JobStatus.COMPLETED
            and isinstance(changes.get("result"), dict)
        ):
            limitations = self.dataset_limitations(existing.request.dataset_id)
            changes["result"] = {
                **changes["result"],
                "limitations": limitations,
                "production_eligibility": limitations.get(
                    "production_eligibility", "blocked"
                ),
            }
            changes["result"], artifact_manifests = (
                self.filesystem.prepare_completed_artifacts(
                    experiment_id,
                    changes["result"],
                )
            )
        updated = existing.model_copy(update={**changes, "updated_at": utc_now()})
        actor = self._actor_provider()
        allowed = {
            JobStatus.QUEUED: {JobStatus.LEASED, JobStatus.CANCELLED},
            JobStatus.LEASED: {
                JobStatus.RUNNING,
                JobStatus.QUEUED,
                JobStatus.CANCELLING,
            },
            JobStatus.RUNNING: {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLING,
            },
            JobStatus.CANCELLING: {JobStatus.CANCELLED, JobStatus.FAILED},
        }
        if updated.status != existing.status and updated.status not in allowed.get(
            existing.status, set()
        ):
            raise WorkerError(
                "invalid_job_transition",
                f"Invalid job transition: {existing.status} -> {updated.status}.",
                stage="job_state",
            )
        if existing.status in {JobStatus.LEASED, JobStatus.RUNNING} and not lease_owner:
            raise WorkerError(
                "lease_owner_required",
                "A matching lease owner is required for this job transition.",
                stage="lease",
            )
        with self.pool.connection() as connection, connection.transaction():
            if artifact_manifests:
                usage = int(
                    connection.execute(
                        """
                        SELECT COALESCE(SUM(size_bytes), 0)
                        FROM nexus_domain.artifacts
                        WHERE actor_key = %s AND deleted_at IS NULL
                        """,
                        (actor,),
                    ).fetchone()[0]
                )
                existing_artifacts = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT artifact_id FROM nexus_domain.artifacts
                        WHERE actor_key = %s AND artifact_id = ANY(%s)
                        """,
                        (
                            actor,
                            [item["artifact_id"] for item in artifact_manifests],
                        ),
                    ).fetchall()
                }
                additional = sum(
                    int(item["size_bytes"])
                    for item in artifact_manifests
                    if item["artifact_id"] not in existing_artifacts
                )
                if usage + additional > self._actor_storage_limit_bytes:
                    raise WorkerError(
                        "artifact_storage_quota_exceeded",
                        "The actor artifact storage budget has been reached.",
                        stage="artifact_publish",
                    )
            cursor = connection.execute(
                """
                UPDATE nexus_domain.experiments
                SET status = %s, result = %s, error = %s,
                    progress_stage = %s, updated_at = %s,
                    lease_owner = CASE WHEN %s THEN NULL ELSE lease_owner END,
                    lease_expires_at = CASE WHEN %s THEN NULL ELSE lease_expires_at END
                WHERE actor_key = %s AND experiment_id = %s
                  AND status = %s
                  AND (%s OR lease_owner = %s)
                RETURNING experiment_id
                """,
                (
                    updated.status.value,
                    Jsonb(updated.result) if updated.result is not None else None,
                    Jsonb(updated.error.model_dump(mode="json"))
                    if updated.error is not None
                    else None,
                    updated.error.stage if updated.error else updated.status.value,
                    updated.updated_at,
                    updated.status
                    in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED},
                    updated.status
                    in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED},
                    actor,
                    experiment_id,
                    existing.status.value,
                    lease_owner is None,
                    lease_owner,
                ),
            )
            if cursor.fetchone() is None:
                raise KeyError(experiment_id)
            for manifest in artifact_manifests:
                connection.execute(
                    """
                    INSERT INTO nexus_domain.artifacts
                        (actor_key, artifact_id, content_hash, media_type,
                         size_bytes, storage_key, manifest, created_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (actor_key, artifact_id) DO NOTHING
                    """,
                    (
                        actor,
                        manifest["artifact_id"],
                        manifest["content_hash"],
                        manifest["media_type"],
                        manifest["size_bytes"],
                        manifest["storage_key"],
                        Jsonb(manifest),
                        manifest["created_at"],
                        manifest.get("expires_at"),
                    ),
                )
                public_manifest = {
                    key: value
                    for key, value in manifest.items()
                    if key != "storage_key"
                }
                self._lineage_node(
                    connection,
                    actor,
                    manifest["artifact_id"],
                    "artifact",
                    public_manifest,
                )
                self._lineage_edge(
                    connection,
                    actor,
                    "produced_artifact",
                    experiment_id,
                    manifest["artifact_id"],
                )
                self._event(
                    connection,
                    actor,
                    experiment_id,
                    "artifact.created",
                    {
                        "artifact_id": manifest["artifact_id"],
                        "producer_type": "qlib_experiment",
                        "producer_id": experiment_id,
                        "status": "ready",
                        "content_hash": manifest["content_hash"],
                    },
                )
            if updated.status == JobStatus.COMPLETED and updated.result is not None:
                attempt_row = connection.execute(
                    """
                    SELECT MAX(attempt) FROM nexus_domain.job_attempts
                    WHERE actor_key = %s AND experiment_id = %s
                    """,
                    (actor, experiment_id),
                ).fetchone()
                attempt_number = int(attempt_row[0]) if attempt_row and attempt_row[0] else 1
                attempt_id = f"{experiment_id}:attempt:{attempt_number}"
                artifacts_by_type = {
                    str(item["artifact_type"]): str(item["artifact_id"])
                    for item in artifact_manifests
                }
                for group in ("signal_metrics", "portfolio_metrics"):
                    for name, value in (updated.result.get(group) or {}).items():
                        metric_id = _metric_node_id(experiment_id, group, name)
                        definition = _metric_definition(group, name)
                        evidence_artifact_id = artifacts_by_type.get(
                            "predictions" if group == "signal_metrics" else "report"
                        )
                        self._lineage_node(
                            connection,
                            actor,
                            metric_id,
                            "metric",
                            {
                                "experiment_id": experiment_id,
                                "metric_group": group,
                                "name": name,
                                "value": value,
                                "unit": definition["unit"],
                                "meaning": definition["meaning"],
                                "better_direction": definition["better_direction"],
                                "calculation_version": "qlib-result-v1",
                                "dataset_revision_id": existing.request.dataset_id,
                                "specification_digest": _specification_digest(
                                    existing.request
                                ),
                                "attempt": attempt_number,
                                "attempt_id": attempt_id,
                                "artifact_ids": (
                                    [evidence_artifact_id]
                                    if evidence_artifact_id is not None
                                    else []
                                ),
                                "limitations": updated.result.get("limitations", {}),
                            },
                        )
                        self._lineage_edge(
                            connection,
                            actor,
                            "produced_metric",
                            experiment_id,
                            metric_id,
                        )
                        self._lineage_edge(
                            connection,
                            actor,
                            "produced_metric_attempt",
                            attempt_id,
                            metric_id,
                        )
                        if evidence_artifact_id is not None:
                            self._lineage_edge(
                                connection,
                                actor,
                                "supports_metric",
                                evidence_artifact_id,
                                metric_id,
                            )
            if updated.status == JobStatus.RUNNING:
                connection.execute(
                    """
                    UPDATE nexus_domain.job_attempts
                    SET status = 'running', stage = 'execution'
                    WHERE actor_key = %s AND experiment_id = %s
                      AND attempt = (
                          SELECT MAX(attempt) FROM nexus_domain.job_attempts
                          WHERE actor_key = %s AND experiment_id = %s
                      )
                    """,
                    (actor, experiment_id, actor, experiment_id),
                )
            elif updated.status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                connection.execute(
                    """
                    UPDATE nexus_domain.job_attempts
                    SET status = %s, stage = %s, finished_at = now(), safe_error = %s
                    WHERE actor_key = %s AND experiment_id = %s
                      AND attempt = (
                          SELECT MAX(attempt) FROM nexus_domain.job_attempts
                          WHERE actor_key = %s AND experiment_id = %s
                      )
                    """,
                    (
                        updated.status.value,
                        updated.error.stage if updated.error else updated.status.value,
                        Jsonb(updated.error.model_dump(mode="json"))
                        if updated.error
                        else None,
                        actor,
                        experiment_id,
                        actor,
                        experiment_id,
                    ),
                )
            if updated.status == JobStatus.CANCELLED:
                cancellation = {
                    **(existing.cancellation or {}),
                    "completed_at": updated.updated_at.isoformat(),
                    "resulting_state": JobStatus.CANCELLED.value,
                }
                connection.execute(
                    """
                    UPDATE nexus_domain.experiments
                    SET cancellation = %s
                    WHERE actor_key = %s AND experiment_id = %s
                    """,
                    (Jsonb(cancellation), actor, experiment_id),
                )
                updated = updated.model_copy(update={"cancellation": cancellation})
            self._event(
                connection,
                actor,
                experiment_id,
                _event_type_for_status(updated.status),
                _worker_payload(
                    experiment_id,
                    updated.status.value,
                    stage=(updated.error.stage if updated.error else updated.progress_stage),
                ),
            )
        return updated

    @staticmethod
    def _event(connection, actor: str, run_id: str, event_type: str, payload: dict) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{actor}:{run_id}",),
        )
        sequence = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
            FROM nexus_domain.product_events
            WHERE actor_key = %s AND run_id = %s
            """,
            (actor, run_id),
        ).fetchone()[0]
        event_id = f"evt_v1_{uuid4().hex}"
        connection.execute(
            """
            INSERT INTO nexus_domain.product_events
                (actor_key, event_id, schema_version, run_id, sequence,
                 event_type, sensitivity, payload)
            VALUES (%s, %s, 1, %s, %s, %s, 'internal', %s)
            """,
            (actor, event_id, run_id, sequence, event_type, Jsonb(payload)),
        )
        connection.execute(
            """
            INSERT INTO nexus_domain.transactional_outbox
                (actor_key, event_id, topic, payload)
            VALUES (%s, %s, 'product-events', %s)
            """,
            (actor, event_id, Jsonb(payload)),
        )

    def dataset_dir(self, dataset_id: str):
        self._require_registered_dataset(dataset_id)
        return self.filesystem.dataset_dir(dataset_id)

    def artifact_dir(self, experiment_id: str):
        return self.filesystem.artifact_dir(experiment_id)

    def discard_partial_artifacts(self, experiment_id: str) -> None:
        self.filesystem.discard_partial_artifacts(experiment_id)

    def artifact_manifest(self, artifact_id: str) -> dict[str, Any]:
        self.filesystem._validate_artifact_id(artifact_id)
        actor = self._actor_provider()
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT a.manifest, a.expires_at, a.deleted_at, t.reason
                FROM nexus_domain.artifacts a
                LEFT JOIN nexus_domain.artifact_tombstones t
                  ON t.actor_key = a.actor_key AND t.artifact_id = a.artifact_id
                WHERE a.actor_key = %s AND a.artifact_id = %s
                """,
                (actor, artifact_id),
            ).fetchone()
            if row is None:
                tombstone = connection.execute(
                    """
                    SELECT deleted_at, reason, prior_content_hash
                    FROM nexus_domain.artifact_tombstones
                    WHERE actor_key = %s AND artifact_id = %s
                    """,
                    (actor, artifact_id),
                ).fetchone()
                if tombstone is not None:
                    return {
                        "artifact_id": artifact_id,
                        "schema_version": "1",
                        "storage_status": "deleted",
                        "deleted_at": tombstone[0].isoformat(),
                        "reason": tombstone[1],
                        "content_hash": tombstone[2],
                    }
        if row is None:
            raise WorkerError("unknown_artifact", "Unknown artifact.")
        manifest = dict(row[0])
        manifest.pop("storage_key", None)
        if row[2] is not None:
            manifest["storage_status"] = "deleted"
            manifest["deleted_at"] = row[2].isoformat()
            manifest["reason"] = row[3] or "retention_policy"
        elif row[1] is not None:
            from worker.models import utc_now

            if row[1] <= utc_now():
                manifest["storage_status"] = "expired"
                manifest["reason"] = "retention_policy"
        return manifest

    def artifact_path(self, artifact_id: str):
        manifest = self.artifact_manifest(artifact_id)
        if manifest.get("storage_status") in {"expired", "deleted"}:
            raise WorkerError(
                f"artifact_{manifest['storage_status']}",
                "Artifact is no longer available.",
                stage="artifact_read",
            )
        return self.filesystem.artifact_path(artifact_id)

    def close(self) -> None:
        self.pool.close()

    @staticmethod
    def _lineage_node(
        connection,
        actor: str,
        node_id: str,
        node_type: str,
        properties: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO nexus_domain.lineage_nodes
                (actor_key, node_id, node_type, schema_version, properties)
            VALUES (%s, %s, %s, '1', %s)
            ON CONFLICT (actor_key, node_id) DO NOTHING
            """,
            (actor, node_id, node_type, Jsonb(properties)),
        )

    @staticmethod
    def _lineage_edge(
        connection,
        actor: str,
        edge_type: str,
        source_node_id: str,
        target_node_id: str,
    ) -> None:
        digest = hashlib.sha256(
            f"{actor}:{edge_type}:{source_node_id}:{target_node_id}".encode()
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO nexus_domain.lineage_edges
                (actor_key, edge_id, edge_type, source_node_id,
                 target_node_id, properties)
            VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)
            ON CONFLICT (actor_key, edge_type, source_node_id, target_node_id)
            DO NOTHING
            """,
            (
                actor,
                f"led_v1_{digest[:32]}",
                edge_type,
                source_node_id,
                target_node_id,
            ),
        )


def _event_type_for_status(status: JobStatus) -> str:
    return {
        JobStatus.QUEUED: "worker.requeued",
        JobStatus.LEASED: "worker.leased",
        JobStatus.RUNNING: "worker.started",
        JobStatus.COMPLETED: "worker.completed",
        JobStatus.FAILED: "worker.failed",
        JobStatus.CANCELLING: "worker.cancellation_requested",
        JobStatus.CANCELLED: "worker.cancelled",
        JobStatus.EXPIRED: "worker.expired",
    }[status]


def _metric_node_id(experiment_id: str, group: str, name: str) -> str:
    digest = hashlib.sha256(f"{experiment_id}:{group}:{name}".encode()).hexdigest()
    return f"lin_v1_{digest[:32]}"


def _specification_digest(request: Any) -> str:
    specification = request.model_dump(mode="json")
    return content_hash(
        "experiment_specification",
        {
            key: value
            for key, value in specification.items()
            if key not in {"idempotency_key", "arguments_digest"}
        },
    )


def _metric_definition(group: str, name: str) -> dict[str, str]:
    definitions = {
        ("signal_metrics", "ic"): (
            "Mean Pearson correlation between predictions and forward labels.",
            "higher",
        ),
        ("signal_metrics", "icir"): (
            "Mean information coefficient divided by its time-series standard deviation.",
            "higher",
        ),
        ("signal_metrics", "rank_ic"): (
            "Mean Spearman rank correlation between predictions and forward labels.",
            "higher",
        ),
        ("signal_metrics", "rank_icir"): (
            "Mean rank information coefficient divided by its standard deviation.",
            "higher",
        ),
        ("portfolio_metrics", "annualized_return"): (
            "Annualized simulated portfolio return over the test segment.",
            "higher",
        ),
        ("portfolio_metrics", "excess_return_after_cost"): (
            "Annualized simulated excess return after configured transaction costs.",
            "higher",
        ),
        ("portfolio_metrics", "information_ratio"): (
            "Risk-adjusted simulated excess return after configured costs.",
            "higher",
        ),
        ("portfolio_metrics", "max_drawdown"): (
            "Largest peak-to-trough simulated portfolio decline.",
            "closer_to_zero",
        ),
        ("portfolio_metrics", "turnover"): (
            "Mean fraction of the simulated portfolio traded per rebalance.",
            "context_dependent",
        ),
    }
    meaning, direction = definitions.get(
        (group, name),
        ("Worker-produced metric; consult its calculation version.", "context_dependent"),
    )
    return {"unit": "ratio", "meaning": meaning, "better_direction": direction}


def _worker_payload(
    experiment_id: str,
    status: str,
    *,
    stage: str | None = None,
    recovery: bool = False,
    reason_category: str | None = None,
) -> dict[str, Any]:
    return {
        "worker": "quant-worker",
        "job_type": "qlib_experiment",
        "job_id": experiment_id,
        "status": status,
        "stage": stage,
        "recovery": recovery,
        "reason_category": reason_category,
    }
