"""Deterministic OpenBB-to-Qlib workflow services with durable resume state."""

from __future__ import annotations

from typing import Any, Protocol

from source.contracts import PrepareQlibDatasetIntent, RunQlibExperimentIntent
from source.contracts.identity import action_fingerprint, new_runtime_id
from source.domain import WorkflowRecord, WorkflowRepository
from source.integrations.worker_client import WorkerClientError


class QuantDataGateway(Protocol):
    async def fetch_openbb_ohlcv(self, request: dict[str, Any]) -> dict[str, Any]: ...
    async def validate_market_data(self, staging_id: str) -> dict[str, Any]: ...
    async def build_qlib_dataset(self, request: dict[str, Any]) -> dict[str, Any]: ...
    async def validate_qlib_dataset(self, revision_id: str) -> dict[str, Any]: ...


class QuantExperimentGateway(Protocol):
    async def validate_dataset(self, dataset_id: str, universe: str) -> dict[str, Any]: ...
    async def submit_experiment(self, request: dict[str, Any]) -> dict[str, Any]: ...


class PrepareQlibDatasetWorkflow:
    def __init__(
        self,
        repository: WorkflowRepository,
        gateway: QuantDataGateway,
    ) -> None:
        self.repository = repository
        self.gateway = gateway

    async def execute(
        self,
        *,
        actor_key: str,
        intent: PrepareQlibDatasetIntent,
        idempotency_key: str | None = None,
        force_new: bool = False,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = intent.model_dump(mode="json")
        digest = _workflow_digest(
            actor_key,
            "prepare_qlib_dataset",
            normalized,
            "quant-data-worker",
        )
        key = _idempotency_key(digest, idempotency_key, force_new)
        record, _created = await self.repository.start(
            actor_key=actor_key,
            workflow_type="prepare_qlib_dataset",
            idempotency_key=key,
            intent_digest=digest,
            normalized_input=normalized,
            thread_id=thread_id,
            run_id=run_id,
        )
        if record.state == "completed":
            return _success(record)
        record = await _resume_retryable(self.repository, record)
        if record.state == "failed":
            return _failure(record)
        try:
            if "staging_revision_id" not in record.output:
                fetched = await self.gateway.fetch_openbb_ohlcv(
                    {
                        key: normalized[key]
                        for key in (
                            "symbols",
                            "start_date",
                            "end_date",
                            "provider",
                            "frequency",
                            "adjustment",
                        )
                    }
                    | {
                        "idempotency_key": (
                            "ingestion:"
                            + action_fingerprint({"workflow_key": key}).removeprefix(
                                "act_v1_"
                            )
                        )
                    }
                )
                record = await self.repository.transition(
                    record,
                    allowed_from={"requested"},
                    to_state="staged",
                    stage="fetch_or_resolve_staging_revision",
                    output_patch={
                        "staging_revision_id": fetched["staging_revision_id"],
                        "request_fingerprint": fetched["request_fingerprint"],
                        "staging_content_hash": fetched["content_hash"],
                        "staging_warnings": fetched.get("warnings", []),
                    },
                )
            if not record.output.get("staging_validated"):
                validation = await self.gateway.validate_market_data(
                    record.output["staging_revision_id"]
                )
                if not validation.get("valid"):
                    return await self._reject(
                        record,
                        "invalid_staging_revision",
                        "Staging revision failed deterministic validation.",
                        "validate_staging_revision",
                    )
                record = await self.repository.transition(
                    record,
                    allowed_from={"staged"},
                    to_state="staging_validated",
                    stage="validate_staging_revision",
                    output_patch={
                        "staging_validated": True,
                        "staging_quality_checks": validation.get("quality_checks", {}),
                    },
                )
            if "dataset_revision_id" not in record.output:
                built = await self.gateway.build_qlib_dataset(
                    {
                        "dataset_staging_id": record.output["staging_revision_id"],
                        "dataset_alias": normalized["dataset_alias"],
                        "universe_name": normalized["universe_name"],
                        "include_fields": normalized["include_fields"],
                    }
                )
                record = await self.repository.transition(
                    record,
                    allowed_from={"staging_validated"},
                    to_state="dataset_built",
                    stage="build_or_resolve_dataset_revision",
                    output_patch={
                        "dataset_revision_id": built["dataset_revision_id"],
                        "dataset_alias": built["dataset_alias"],
                        "manifest_hash": built["manifest_hash"],
                        "dataset_warnings": built.get("warnings", []),
                    },
                )
            validation = await self.gateway.validate_qlib_dataset(
                record.output["dataset_revision_id"]
            )
            if not validation.get("valid") or not validation.get("manifest_verified"):
                return await self._reject(
                    record,
                    "invalid_dataset_revision",
                    "Dataset revision or immutable manifest failed validation.",
                    "validate_dataset_manifest",
                )
            record = await self.repository.transition(
                record,
                allowed_from={"dataset_built"},
                to_state="completed",
                stage="persist_outcome",
                output_patch={
                    "dataset_validated": True,
                    "manifest_verified": True,
                    "coverage": {
                        "start_date": validation.get("start_date"),
                        "end_date": validation.get("end_date"),
                        "max_test_end": validation.get("max_test_end"),
                        "trading_days": validation.get("trading_days"),
                    },
                    "limitations": validation.get("limitations", {}),
                    "production_eligibility": validation.get(
                        "production_eligibility", "blocked"
                    ),
                },
            )
            return _success(record)
        except WorkerClientError as exc:
            return await _worker_failure(self.repository, record, exc)

    async def _reject(
        self,
        record: WorkflowRecord,
        code: str,
        message: str,
        stage: str,
    ) -> dict[str, Any]:
        failed = await self.repository.transition(
            record,
            allowed_from={record.state},
            to_state="failed",
            stage=stage,
            error={"code": code, "message": message, "retryable": False, "stage": stage},
        )
        return _failure(failed)


class RunQlibExperimentWorkflow:
    def __init__(
        self,
        repository: WorkflowRepository,
        gateway: QuantExperimentGateway,
    ) -> None:
        self.repository = repository
        self.gateway = gateway

    async def execute(
        self,
        *,
        actor_key: str,
        intent: RunQlibExperimentIntent,
        idempotency_key: str | None = None,
        force_new: bool = False,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = intent.model_dump(mode="json")
        digest = _workflow_digest(
            actor_key,
            "run_qlib_experiment",
            normalized,
            "quant-worker",
        )
        key = _idempotency_key(digest, idempotency_key, force_new)
        record, _created = await self.repository.start(
            actor_key=actor_key,
            workflow_type="run_qlib_experiment",
            idempotency_key=key,
            intent_digest=digest,
            normalized_input=normalized,
            thread_id=thread_id,
            run_id=run_id,
        )
        if record.state == "completed":
            return _success(record)
        record = await _resume_retryable(self.repository, record)
        if record.state == "failed":
            return _failure(record)
        try:
            if not record.output.get("dataset_validated"):
                validation = await self.gateway.validate_dataset(
                    normalized["dataset_revision_id"],
                    normalized["universe"],
                )
                if not validation.get("valid") or not validation.get(
                    "manifest_verified"
                ):
                    failed = await self.repository.transition(
                        record,
                        allowed_from={record.state},
                        to_state="failed",
                        stage="validate_manifest_and_limitations",
                        error={
                            "code": "dataset_not_ready",
                            "message": "Dataset or manifest failed deterministic validation.",
                            "retryable": False,
                            "stage": "validate_manifest_and_limitations",
                        },
                    )
                    return _failure(failed)
                record = await self.repository.transition(
                    record,
                    allowed_from={"requested"},
                    to_state="dataset_validated",
                    stage="validate_manifest_and_limitations",
                    output_patch={
                        "dataset_validated": True,
                        "manifest_hash": validation.get("manifest_hash"),
                        "max_test_end": validation.get("max_test_end"),
                        "limitations": validation.get("limitations", {}),
                        "production_eligibility": validation.get(
                            "production_eligibility", "blocked"
                        ),
                    },
                )
            request = {
                "dataset_id": normalized["dataset_revision_id"],
                "universe": normalized["universe"],
                "feature_set": "Alpha158",
                "model": normalized["model"],
                "train": normalized["train"],
                "valid": normalized["valid"],
                "test": normalized["test"],
                "strategy": {
                    "type": "topk_dropout",
                    "topk": normalized["topk"],
                    "n_drop": normalized["n_drop"],
                    "account": normalized["account"],
                    "open_cost": normalized["open_cost"],
                    "close_cost": normalized["close_cost"],
                    "min_cost": normalized["min_cost"],
                },
                "idempotency_key": key,
                "arguments_digest": digest,
            }
            accepted = await self.gateway.submit_experiment(request)
            record = await self.repository.transition(
                record,
                allowed_from={"dataset_validated"},
                to_state="completed",
                stage="return_durable_job_reference",
                output_patch={
                    "experiment_id": accepted["experiment_id"],
                    "experiment_status": accepted["status"],
                    "dataset_revision_id": normalized["dataset_revision_id"],
                },
            )
            return _success(record)
        except WorkerClientError as exc:
            return await _worker_failure(self.repository, record, exc)


def _workflow_digest(
    actor_key: str,
    operation: str,
    normalized: dict[str, Any],
    target: str,
) -> str:
    return action_fingerprint(
        {
            "schema_version": "1",
            "actor_key": actor_key,
            "tool_name": operation,
            "normalized_arguments": normalized,
            "target_boundary": target,
        }
    )


def _idempotency_key(digest: str, supplied: str | None, force_new: bool) -> str:
    if force_new:
        return new_runtime_id("workflow")
    if supplied:
        return supplied
    return f"workflow:{digest.removeprefix('act_v1_')}"


async def _resume_retryable(
    repository: WorkflowRepository,
    record: WorkflowRecord,
) -> WorkflowRecord:
    if record.state != "failed" or not (record.error or {}).get("retryable"):
        return record
    output = record.output
    if "dataset_revision_id" in output:
        state = "dataset_built"
    elif output.get("staging_validated"):
        state = "staging_validated"
    elif "staging_revision_id" in output:
        state = "staged"
    elif output.get("dataset_validated"):
        state = "dataset_validated"
    else:
        state = "requested"
    return await repository.transition(
        record,
        allowed_from={"failed"},
        to_state=state,
        stage="resume",
    )


async def _worker_failure(
    repository: WorkflowRepository,
    record: WorkflowRecord,
    exc: WorkerClientError,
) -> dict[str, Any]:
    retryable = (
        exc.retryable
        if exc.retryable is not None
        else exc.code in {"quant_worker_timeout", "quant_worker_unavailable"}
    )
    failed = await repository.transition(
        record,
        allowed_from={record.state},
        to_state="failed",
        stage=exc.stage or record.stage,
        error={
            "code": exc.code,
            "message": exc.message,
            "retryable": retryable,
            "stage": exc.stage or record.stage,
        },
    )
    return _failure(failed)


def _success(record: WorkflowRecord) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": "1",
        "workflow_id": record.workflow_id,
        "workflow_type": record.workflow_type,
        "state": record.state,
        "stage": record.stage,
        **record.output,
    }


def _failure(record: WorkflowRecord) -> dict[str, Any]:
    return {
        "ok": False,
        "schema_version": "1",
        "workflow_id": record.workflow_id,
        "workflow_type": record.workflow_type,
        "state": record.state,
        "stage": record.stage,
        "error": record.error,
        "partial_output": record.output,
    }
