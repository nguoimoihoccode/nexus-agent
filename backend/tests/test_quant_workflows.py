"""Model-free tests for deterministic composite quant workflows."""

import unittest
from dataclasses import replace
from datetime import date

from source.application import PrepareQlibDatasetWorkflow, RunQlibExperimentWorkflow
from source.contracts import PrepareQlibDatasetIntent, RunQlibExperimentIntent
from source.domain import WorkflowConflict, WorkflowRecord
from source.integrations.worker_client import WorkerClientError
from source.tools.quant import QuantTools
from source.tools.quant_data import QuantDataTools

ACTOR = "v1-" + "1" * 64


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self.records = {}
        self.transitions = []

    async def start(
        self,
        *,
        actor_key,
        workflow_type,
        idempotency_key,
        intent_digest,
        normalized_input,
        thread_id=None,
        run_id=None,
    ):
        del thread_id, run_id
        key = (actor_key, workflow_type, idempotency_key)
        if key in self.records:
            record = self.records[key]
            if record.intent_digest != intent_digest:
                raise WorkflowConflict("idempotency_conflict")
            return record, False
        record = WorkflowRecord(
            actor_key=actor_key,
            workflow_id=f"wfl_v1_{len(self.records) + 1:032x}",
            workflow_type=workflow_type,
            idempotency_key=idempotency_key,
            intent_digest=intent_digest,
            state="requested",
            stage="normalize",
            normalized_input=normalized_input,
        )
        self.records[key] = record
        return record, True

    async def transition(
        self,
        record,
        *,
        allowed_from,
        to_state,
        stage,
        output_patch=None,
        error=None,
    ):
        key = (record.actor_key, record.workflow_type, record.idempotency_key)
        current = self.records[key]
        if current.state not in allowed_from:
            if current.state == to_state:
                return current
            raise WorkflowConflict(f"{current.state} -> {to_state}")
        updated = replace(
            current,
            state=to_state,
            stage=stage,
            output={**current.output, **(output_patch or {})},
            error=error,
        )
        self.records[key] = updated
        self.transitions.append((current.state, to_state, stage))
        return updated


def dataset_intent() -> PrepareQlibDatasetIntent:
    return PrepareQlibDatasetIntent(
        symbols=["MSFT", "AAPL"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
        dataset_alias="research-daily",
        universe_name="custom_us_2",
    )


def experiment_intent() -> RunQlibExperimentIntent:
    return RunQlibExperimentIntent(
        dataset_revision_id="dsr_v1_" + "1" * 24,
        universe="custom_us_2",
        train={"start": date(2024, 1, 1), "end": date(2024, 1, 10)},
        valid={"start": date(2024, 1, 11), "end": date(2024, 1, 15)},
        test={"start": date(2024, 1, 16), "end": date(2024, 1, 20)},
    )


class DatasetGateway:
    def __init__(self, *, invalid_staging=False, fail_validation_once=False) -> None:
        self.calls = []
        self.invalid_staging = invalid_staging
        self.fail_validation_once = fail_validation_once

    async def fetch_openbb_ohlcv(self, request):
        self.calls.append("fetch")
        return {
            "staging_revision_id": "stg_v1_" + "2" * 24,
            "request_fingerprint": "rqf_v1_" + "3" * 64,
            "content_hash": "sha256:" + "4" * 64,
            "warnings": [],
        }

    async def validate_market_data(self, staging_id):
        del staging_id
        self.calls.append("validate_staging")
        if self.fail_validation_once:
            self.fail_validation_once = False
            raise WorkerClientError(
                "quant_worker_unavailable",
                "Worker unavailable.",
                stage="validate_staging_revision",
                retryable=True,
            )
        return {"valid": not self.invalid_staging, "quality_checks": {}}

    async def build_qlib_dataset(self, request):
        self.calls.append("build")
        return {
            "dataset_revision_id": "dsr_v1_" + "5" * 24,
            "dataset_alias": request["dataset_alias"],
            "manifest_hash": "sha256:" + "6" * 64,
            "warnings": [],
        }

    async def validate_qlib_dataset(self, revision_id):
        del revision_id
        self.calls.append("validate_dataset")
        return {
            "valid": True,
            "manifest_verified": True,
            "start_date": "2024-01-01",
            "end_date": "2024-02-01",
            "max_test_end": "2024-01-30",
            "trading_days": 22,
        }


class ExperimentGateway:
    def __init__(self, *, valid=True, submit_error=None) -> None:
        self.valid = valid
        self.submit_error = submit_error
        self.calls = []

    async def validate_dataset(self, dataset_id, universe):
        self.calls.append("validate")
        return {
            "dataset_id": dataset_id,
            "universe": universe,
            "valid": self.valid,
            "manifest_verified": self.valid,
            "manifest_hash": "sha256:" + "7" * 64,
            "max_test_end": "2024-01-30",
        }

    async def submit_experiment(self, request):
        self.calls.append("submit")
        if self.submit_error:
            raise self.submit_error
        self.request = request
        return {
            "experiment_id": "exp_v1_" + "8" * 32,
            "status": "queued",
            "dataset_id": request["dataset_id"],
        }


class QuantWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_dataset_workflow_enforces_order_and_reuses_completed_result(self):
        repository = InMemoryWorkflowRepository()
        gateway = DatasetGateway()
        workflow = PrepareQlibDatasetWorkflow(repository, gateway)

        first = await workflow.execute(actor_key=ACTOR, intent=dataset_intent())
        second = await workflow.execute(actor_key=ACTOR, intent=dataset_intent())

        self.assertTrue(first["ok"])
        self.assertEqual(first["dataset_revision_id"], "dsr_v1_" + "5" * 24)
        self.assertEqual(second["workflow_id"], first["workflow_id"])
        self.assertEqual(
            gateway.calls,
            ["fetch", "validate_staging", "build", "validate_dataset"],
        )

    async def test_invalid_staging_stops_before_dataset_build(self):
        gateway = DatasetGateway(invalid_staging=True)
        result = await PrepareQlibDatasetWorkflow(
            InMemoryWorkflowRepository(), gateway
        ).execute(actor_key=ACTOR, intent=dataset_intent())

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_staging_revision")
        self.assertEqual(gateway.calls, ["fetch", "validate_staging"])

    async def test_retryable_resume_does_not_repeat_committed_fetch(self):
        repository = InMemoryWorkflowRepository()
        gateway = DatasetGateway(fail_validation_once=True)
        workflow = PrepareQlibDatasetWorkflow(repository, gateway)

        failed = await workflow.execute(actor_key=ACTOR, intent=dataset_intent())
        completed = await workflow.execute(actor_key=ACTOR, intent=dataset_intent())

        self.assertTrue(failed["error"]["retryable"])
        self.assertTrue(completed["ok"])
        self.assertEqual(gateway.calls.count("fetch"), 1)
        self.assertEqual(gateway.calls.count("build"), 1)

    async def test_experiment_validation_failure_issues_no_submit(self):
        gateway = ExperimentGateway(valid=False)
        result = await RunQlibExperimentWorkflow(
            InMemoryWorkflowRepository(), gateway
        ).execute(actor_key=ACTOR, intent=experiment_intent())

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "dataset_not_ready")
        self.assertEqual(gateway.calls, ["validate"])

    async def test_preflight_worker_rejection_returns_no_experiment_id(self):
        error = WorkerClientError(
            "invalid_date_range",
            "Date ranges exceed coverage.",
            stage="validation",
            retryable=False,
        )
        result = await RunQlibExperimentWorkflow(
            InMemoryWorkflowRepository(),
            ExperimentGateway(submit_error=error),
        ).execute(actor_key=ACTOR, intent=experiment_intent())

        self.assertFalse(result["ok"])
        self.assertNotIn("experiment_id", result["partial_output"])

    def test_model_visible_toolsets_exclude_lower_level_side_effects(self):
        quant_data_names = {tool.name for tool in QuantDataTools.tools}
        quant_names = {tool.name for tool in QuantTools.tools}

        self.assertIn("prepare_qlib_dataset", quant_data_names)
        self.assertNotIn("fetch_openbb_ohlcv", quant_data_names)
        self.assertNotIn("build_qlib_dataset", quant_data_names)
        self.assertIn("run_governed_qlib_experiment", quant_names)
        self.assertNotIn("run_qlib_experiment", quant_names)


if __name__ == "__main__":
    unittest.main()
