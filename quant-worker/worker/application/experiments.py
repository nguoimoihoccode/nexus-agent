"""Experiment lifecycle orchestration independent of HTTP and Qlib details."""

import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable
from uuid import uuid4

from worker.domain import (
    DatasetInspection,
    WorkerError,
    content_hash,
    new_experiment_id,
    preflight_experiment,
)
from worker.models import (
    DatasetInfo,
    DatasetValidation,
    ExperimentAccepted,
    ExperimentRecord,
    ExperimentRequest,
    JobStatus,
    WorkerError as WorkerErrorModel,
    utc_now,
)
from worker.ports import ExperimentRepository, ExperimentRunner
from worker.security import actor_context

EventSink = Callable[..., None]


class ExperimentApplicationService:
    """Coordinate validation, idempotency, execution, and record state."""

    def __init__(
        self,
        repository: ExperimentRepository,
        runner: ExperimentRunner,
        *,
        actor_provider: Callable[[], str],
        event_sink: EventSink,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self._actor_provider = actor_provider
        self._event_sink = event_sink
        self.executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="qlib",
        )
        self._lock = Lock()
        self._active_requests: dict[str, str] = {}
        self._lease_owner = f"worker-{uuid4().hex}"
        self._lease_seconds = 30
        self._scheduled_durable: set[tuple[str, str]] = set()
        self._cancellation_signals: dict[tuple[str, str], Event] = {}
        self._coordinator_stop = Event()
        self._coordinator: Thread | None = None

    @property
    def data_dir(self) -> Path:
        return self.repository.data_dir

    @property
    def artifacts_dir(self) -> Path:
        return self.repository.artifacts_dir

    def close(self, *, wait: bool = False) -> None:
        self._coordinator_stop.set()
        if self._coordinator is not None:
            self._coordinator.join(timeout=1)
        with self._lock:
            for signal in self._cancellation_signals.values():
                signal.set()
        self.executor.shutdown(wait=wait, cancel_futures=True)
        self.repository.close()

    def start_coordinator(self) -> None:
        if getattr(self.repository, "pending_jobs", None) is None:
            self.recover_interrupted()
            return
        if self._coordinator is not None:
            return
        self._coordinator = Thread(
            target=self._coordinate_durable_jobs,
            daemon=True,
            name="quant-job-coordinator",
        )
        self._coordinator.start()

    def readiness(self) -> bool:
        return self.repository.readiness()

    def recover_interrupted(self) -> int:
        recovered = self.repository.recover_interrupted()
        expire_artifacts = getattr(self.repository, "expire_artifacts", None)
        if expire_artifacts is not None:
            recovered += expire_artifacts()
        pending_jobs = getattr(self.repository, "pending_jobs", None)
        if pending_jobs is not None:
            for actor, experiment_id in pending_jobs():
                self._schedule_durable(actor, experiment_id)
        return recovered

    def list_datasets(self) -> list[DatasetInfo]:
        return self.repository.list_datasets()

    def validate_dataset(
        self,
        dataset_id: str,
        universe: str,
    ) -> DatasetValidation:
        return self.repository.validate_dataset(dataset_id, universe)

    def inspect_dataset(
        self,
        dataset_id: str,
        universe: str,
    ) -> DatasetInspection:
        return self.repository.inspect_dataset(dataset_id, universe)

    @staticmethod
    def preflight_experiment(
        request: ExperimentRequest,
        inspection: DatasetInspection,
    ) -> list[str]:
        return preflight_experiment(request, inspection)

    def submit_experiment(self, request: ExperimentRequest) -> ExperimentAccepted:
        inspection = self.inspect_dataset(request.dataset_id, request.universe)
        if not inspection.ready:
            raise WorkerError(
                "dataset_not_ready",
                "Dataset is not ready.",
                stage="validation",
            )
        errors = self.preflight_experiment(request, inspection)
        if errors:
            raise WorkerError(
                "invalid_date_range",
                "; ".join(errors),
                stage="validation",
            )

        actor_key = self._actor_provider()
        reserve_experiment = getattr(self.repository, "reserve_experiment", None)
        if reserve_experiment is not None:
            specification = request.model_dump(mode="json")
            digest = request.arguments_digest or content_hash(
                "experiment_submission",
                {
                    key: value
                    for key, value in specification.items()
                    if key not in {"idempotency_key", "arguments_digest"}
                },
            )
            idempotency_key = request.idempotency_key or (
                f"experiment:{digest.split(':', 1)[-1]}"
            )
            record, created = reserve_experiment(
                request,
                idempotency_key=idempotency_key,
                arguments_digest=digest,
            )
            if record.status == JobStatus.QUEUED:
                self._schedule_durable(actor_key, record.experiment_id)
            self._event_sink(
                "experiment.accepted" if created else "experiment.reused",
                experiment_id=record.experiment_id,
                dataset_id=record.request.dataset_id,
                status=record.status,
            )
            return ExperimentAccepted(
                experiment_id=record.experiment_id,
                status=record.status,
                dataset_id=record.request.dataset_id,
            )

        request_key = f"{actor_key}:{request.model_dump_json()}"
        with self._lock:
            existing_id = self._active_requests.get(request_key)
            if existing_id:
                existing = self.load_record(existing_id)
                if existing and existing.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                    self._event_sink(
                        "experiment.reused",
                        experiment_id=existing.experiment_id,
                        dataset_id=existing.request.dataset_id,
                        status=existing.status,
                    )
                    return ExperimentAccepted(
                        experiment_id=existing.experiment_id,
                        status=existing.status,
                        dataset_id=existing.request.dataset_id,
                    )
                self._active_requests.pop(request_key, None)

            active_for_actor = [
                experiment_id
                for key, experiment_id in self._active_requests.items()
                if key.startswith(f"{actor_key}:")
                and (record := self.load_record(experiment_id)) is not None
                and record.status in {JobStatus.QUEUED, JobStatus.RUNNING}
            ]
            if active_for_actor:
                raise WorkerError(
                    "experiment_quota_exceeded",
                    "Only one queued or running experiment is allowed per user.",
                    stage="quota",
                )

            experiment_id = new_experiment_id()
            now = utc_now()
            record = ExperimentRecord(
                experiment_id=experiment_id,
                status=JobStatus.QUEUED,
                created_at=now,
                updated_at=now,
                request=request,
            )
            self.repository.save_record(record)
            self._active_requests[request_key] = experiment_id
            context = copy_context()
            self.executor.submit(
                context.run,
                self._execute,
                experiment_id,
                request_key,
            )
            self._event_sink(
                "experiment.submitted",
                experiment_id=experiment_id,
                dataset_id=request.dataset_id,
                model=request.model,
            )
            return ExperimentAccepted(
                experiment_id=experiment_id,
                status=JobStatus.QUEUED,
                dataset_id=request.dataset_id,
            )

    def experiment_result(self, experiment_id: str) -> dict[str, Any]:
        record = self.require_record(experiment_id)
        if record.status == JobStatus.COMPLETED:
            return {
                "experiment_id": record.experiment_id,
                "status": record.status,
                **(record.result or {}),
            }
        payload: dict[str, Any] = {
            "experiment_id": record.experiment_id,
            "status": record.status,
        }
        if record.error is not None:
            payload["error"] = record.error.model_dump()
        return payload

    def artifact_manifest(self, artifact_id: str) -> dict[str, Any]:
        return self.repository.artifact_manifest(artifact_id)

    def artifact_path(self, artifact_id: str):
        return self.repository.artifact_path(artifact_id)

    def cancel_experiment(
        self,
        experiment_id: str,
        *,
        reason_category: str,
    ) -> ExperimentRecord:
        record = self.repository.request_cancellation(
            experiment_id,
            reason_category=reason_category,
        )
        actor = self._actor_provider()
        if record.status == JobStatus.CANCELLING:
            with self._lock:
                signal = self._cancellation_signals.setdefault(
                    (actor, experiment_id),
                    Event(),
                )
                signal.set()
        self._event_sink(
            "experiment.cancellation_requested",
            experiment_id=experiment_id,
            reason_category=reason_category,
            status=record.status,
        )
        return record

    def _discard_partial_artifacts(self, experiment_id: str) -> None:
        discard = getattr(self.repository, "discard_partial_artifacts", None)
        if discard is not None:
            discard(experiment_id)

    def require_record(self, experiment_id: str) -> ExperimentRecord:
        try:
            record = self.load_record(experiment_id)
        except ValueError as exc:
            raise WorkerError("invalid_experiment_id", "Invalid experiment ID.") from exc
        if record is None:
            raise WorkerError("unknown_experiment", "Unknown experiment.")
        return record

    def load_record(self, experiment_id: str) -> ExperimentRecord | None:
        return self.repository.load_record(experiment_id)

    def _execute(self, experiment_id: str, request_key: str) -> None:
        actor = self._actor_provider()
        existing = self.repository.load_record(experiment_id)
        if existing is None or existing.status == JobStatus.CANCELLED:
            with self._lock:
                if self._active_requests.get(request_key) == experiment_id:
                    self._active_requests.pop(request_key, None)
            return
        cancellation = Event()
        with self._lock:
            self._cancellation_signals[(actor, experiment_id)] = cancellation
        record = self.repository.update_record(
            experiment_id,
            status=JobStatus.RUNNING,
            error=None,
        )
        self._event_sink(
            "experiment.started",
            experiment_id=experiment_id,
            dataset_id=record.request.dataset_id,
            model=record.request.model,
        )
        try:
            result = self.runner(
                record.request,
                experiment_id=experiment_id,
                data_dir=self.repository.dataset_dir(record.request.dataset_id),
                artifact_dir=self.repository.artifact_dir(experiment_id),
            )
        except Exception as exc:
            stage = getattr(exc, "stage", None) or "execution"
            self._event_sink(
                "experiment.failed",
                level=logging.ERROR,
                experiment_id=experiment_id,
                dataset_id=record.request.dataset_id,
                error_type=type(exc).__name__,
                stage=stage,
            )
            current = self.repository.load_record(experiment_id)
            if cancellation.is_set() or (
                current is not None and current.status == JobStatus.CANCELLING
            ):
                self.repository.update_record(
                    experiment_id,
                    status=JobStatus.CANCELLED,
                    result=None,
                    error=None,
                )
                self._discard_partial_artifacts(experiment_id)
            else:
                self.repository.update_record(
                    experiment_id,
                    status=JobStatus.FAILED,
                    error=WorkerErrorModel(
                        code="qlib_execution_failed",
                        message=f"Qlib execution failed during {stage}.",
                        stage=stage,
                    ),
                )
        else:
            current = self.repository.load_record(experiment_id)
            if cancellation.is_set() or (
                current is not None and current.status == JobStatus.CANCELLING
            ):
                self.repository.update_record(
                    experiment_id,
                    status=JobStatus.CANCELLED,
                    result=None,
                    error=None,
                )
                self._discard_partial_artifacts(experiment_id)
                self._event_sink(
                    "experiment.cancelled",
                    experiment_id=experiment_id,
                )
            else:
                self._commit_completed(record, result)
        finally:
            with self._lock:
                if self._active_requests.get(request_key) == experiment_id:
                    self._active_requests.pop(request_key, None)
                self._cancellation_signals.pop((actor, experiment_id), None)

    def _execute_durable(self, actor: str, experiment_id: str) -> None:
        try:
            claim = getattr(self.repository, "claim_experiment")
            record = claim(
                actor,
                experiment_id,
                self._lease_owner,
                self._lease_seconds,
            )
            if record is None:
                return
            cancellation = Event()
            with self._lock:
                self._cancellation_signals[(actor, experiment_id)] = cancellation
            try:
                record = self.repository.update_record(
                    experiment_id,
                    status=JobStatus.RUNNING,
                    error=None,
                    lease_owner=self._lease_owner,
                )
            except (KeyError, WorkerError):
                current = self.repository.load_record(experiment_id)
                if current and current.status == JobStatus.CANCELLING:
                    self.repository.update_record(
                        experiment_id,
                        status=JobStatus.CANCELLED,
                        error=None,
                    )
                    self._discard_partial_artifacts(experiment_id)
                    return
                raise
            self._event_sink(
                "experiment.started",
                experiment_id=experiment_id,
                dataset_id=record.request.dataset_id,
                model=record.request.model,
            )
            stop_heartbeat = Event()
            heartbeat = Thread(
                target=self._heartbeat,
                args=(stop_heartbeat, actor, experiment_id),
                daemon=True,
                name=f"heartbeat-{experiment_id}",
            )
            heartbeat.start()
            try:
                runtime_versions = getattr(self.runner, "runtime_versions", None)
                record_versions = getattr(
                    self.repository,
                    "record_runtime_versions",
                    None,
                )
                if runtime_versions is not None and record_versions is not None:
                    record_versions(
                        actor,
                        experiment_id,
                        self._lease_owner,
                        runtime_versions(record.request),
                    )
                controllable = getattr(self.runner, "run_controllable", None)
                if controllable is not None:
                    result = controllable(
                        record.request,
                        experiment_id=experiment_id,
                        data_dir=self.repository.dataset_dir(
                            record.request.dataset_id
                        ),
                        artifact_dir=self.repository.artifact_dir(experiment_id),
                        cancellation=cancellation,
                    )
                else:
                    result = self.runner(
                        record.request,
                        experiment_id=experiment_id,
                        data_dir=self.repository.dataset_dir(
                            record.request.dataset_id
                        ),
                        artifact_dir=self.repository.artifact_dir(experiment_id),
                    )
            except Exception as exc:
                stage = getattr(exc, "stage", None) or "execution"
                if not self._coordinator_stop.is_set():
                    self._event_sink(
                        "experiment.failed",
                        level=logging.ERROR,
                        experiment_id=experiment_id,
                        dataset_id=record.request.dataset_id,
                        error_type=type(exc).__name__,
                        stage=stage,
                    )
                current = self.repository.load_record(experiment_id)
                if self._coordinator_stop.is_set() and getattr(
                    self.repository, "requeue_for_shutdown", None
                ) is not None:
                    self.repository.requeue_for_shutdown(
                        actor,
                        experiment_id,
                        self._lease_owner,
                    )
                    self._discard_partial_artifacts(experiment_id)
                elif cancellation.is_set() or (
                    current is not None and current.status == JobStatus.CANCELLING
                ):
                    self.repository.update_record(
                        experiment_id,
                        status=JobStatus.CANCELLED,
                        result=None,
                        error=None,
                    )
                    self._discard_partial_artifacts(experiment_id)
                else:
                    self.repository.update_record(
                        experiment_id,
                        status=JobStatus.FAILED,
                        error=WorkerErrorModel(
                            code="qlib_execution_failed",
                            message=f"Qlib execution failed during {stage}.",
                            stage=stage,
                        ),
                        lease_owner=self._lease_owner,
                    )
            else:
                current = self.repository.load_record(experiment_id)
                if self._coordinator_stop.is_set() and getattr(
                    self.repository, "requeue_for_shutdown", None
                ) is not None:
                    self.repository.requeue_for_shutdown(
                        actor,
                        experiment_id,
                        self._lease_owner,
                    )
                    self._discard_partial_artifacts(experiment_id)
                elif cancellation.is_set() or (
                    current is not None and current.status == JobStatus.CANCELLING
                ):
                    self.repository.update_record(
                        experiment_id,
                        status=JobStatus.CANCELLED,
                        result=None,
                        error=None,
                    )
                    self._discard_partial_artifacts(experiment_id)
                    self._event_sink(
                        "experiment.cancelled",
                        experiment_id=experiment_id,
                    )
                else:
                    self._commit_completed(
                        record,
                        result,
                        lease_owner=self._lease_owner,
                    )
            finally:
                stop_heartbeat.set()
                heartbeat.join(timeout=1)
        finally:
            with self._lock:
                self._scheduled_durable.discard((actor, experiment_id))
                self._cancellation_signals.pop((actor, experiment_id), None)

    def _heartbeat(self, stop: Event, actor: str, experiment_id: str) -> None:
        heartbeat = getattr(self.repository, "heartbeat")
        while not stop.wait(self._lease_seconds / 3):
            if not heartbeat(
                actor,
                experiment_id,
                self._lease_owner,
                self._lease_seconds,
            ):
                return

    def _commit_completed(
        self,
        record: ExperimentRecord,
        result: dict[str, Any],
        *,
        lease_owner: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": JobStatus.COMPLETED,
            "result": result,
            "error": None,
        }
        if lease_owner is not None:
            values["lease_owner"] = lease_owner
        try:
            self.repository.update_record(record.experiment_id, **values)
        except WorkerError as exc:
            self._discard_partial_artifacts(record.experiment_id)
            failure: dict[str, Any] = {
                "status": JobStatus.FAILED,
                "result": None,
                "error": WorkerErrorModel(
                    code=exc.code,
                    message="Experiment artifacts could not be published.",
                    stage=exc.stage or "artifact_publish",
                    retryable=False,
                ),
            }
            if lease_owner is not None:
                failure["lease_owner"] = lease_owner
            self.repository.update_record(record.experiment_id, **failure)
            self._event_sink(
                "experiment.failed",
                experiment_id=record.experiment_id,
                dataset_id=record.request.dataset_id,
                stage=exc.stage or "artifact_publish",
            )
            return
        self._event_sink(
            "experiment.completed",
            experiment_id=record.experiment_id,
            dataset_id=record.request.dataset_id,
            model=record.request.model,
        )

    def _schedule_durable(self, actor: str, experiment_id: str) -> None:
        key = (actor, experiment_id)
        with self._lock:
            if key in self._scheduled_durable:
                return
            self._scheduled_durable.add(key)
        with actor_context(actor):
            context = copy_context()
            self.executor.submit(
                context.run,
                self._execute_durable,
                actor,
                experiment_id,
            )

    def _coordinate_durable_jobs(self) -> None:
        while not self._coordinator_stop.is_set():
            try:
                self.recover_interrupted()
            except Exception as exc:
                self._event_sink(
                    "experiment.coordinator_failed",
                    level=logging.ERROR,
                    error_type=type(exc).__name__,
                )
            self._coordinator_stop.wait(5)
