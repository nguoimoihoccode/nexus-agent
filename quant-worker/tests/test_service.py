import json
import logging
import os
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from worker.adapters.qlib_runner import (
    CpuCatBoostModel,
    NaSafeLinearModel,
    QlibRunner,
    _analysis_metric,
    isolated_experiment_manager,
)
from worker.models import ExperimentRequest, JobStatus
from worker.domain import (
    CANONICAL_SCHEMA_VERSION,
    QlibStageError,
    WorkerError,
    content_hash,
    revision_id,
    stage,
)
from worker.service import QlibExperimentWorker
from worker.security import actor_context


REQUEST = ExperimentRequest.model_validate(
    {
        "dataset_id": "openbb-demo",
        "universe": "demo",
        "train": {"start": "2020-01-01", "end": "2020-01-05"},
        "valid": {"start": "2020-01-06", "end": "2020-01-10"},
        "test": {"start": "2020-01-11", "end": "2020-01-18"},
    }
)


def write_dataset(
    root: Path,
    *,
    dataset_id: str = "openbb-demo",
    universe: str = "demo",
    symbol: str = "AAPL",
    days: int = 20,
) -> Path:
    dataset_dir = root / dataset_id
    dataset_dir.mkdir()
    _write_provider_layout(dataset_dir, universe=universe, symbol=symbol, days=days)
    return dataset_dir


def _write_provider_layout(
    root: Path,
    *,
    universe: str,
    symbol: str,
    days: int = 20,
) -> None:
    (root / "calendars").mkdir()
    (root / "instruments").mkdir()
    (root / "features").mkdir()
    start = date(2020, 1, 1)
    calendar = "\n".join(str(start + timedelta(days=offset)) for offset in range(days))
    (root / "calendars" / "day.txt").write_text(calendar, encoding="utf-8")
    (root / "instruments" / f"{universe}.txt").write_text(
        f"{symbol}\t2020-01-01\t2020-01-20\n",
        encoding="utf-8",
    )
    feature_dir = root / "features" / symbol.lower()
    feature_dir.mkdir()
    (feature_dir / "close.day.bin").write_bytes(b"")
    (feature_dir / "factor.day.bin").write_bytes(b"")


def write_custom_dataset(
    root: Path,
    dataset_id: str,
    universe: str,
    *,
    days: int = 20,
) -> Path:
    return write_dataset(root, dataset_id=dataset_id, universe=universe, days=days)


def write_manifest_dataset(
    worker: QlibExperimentWorker,
    *,
    universe: str = "manifest_demo",
) -> tuple[str, Path]:
    temporary = write_dataset(
        worker.data_dir,
        dataset_id="temporary-manifest-dataset",
        universe=universe,
    )
    payload = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "source_staging_revision_id": "stg_v1_0123456789abcdef01234567",
        "universe_name": universe,
        "include_fields": ["open", "high", "low", "close", "volume", "factor"],
        "files": worker.repository._manifest_files(temporary),
    }
    digest = content_hash("qlib_dataset_manifest", payload)
    dataset_revision_id = revision_id("dsr", digest)
    target = temporary.parent / dataset_revision_id
    temporary.rename(target)
    manifest = {
        **payload,
        "dataset_revision_id": dataset_revision_id,
        "dataset_alias": "manifest-test",
        "manifest_hash": digest,
        "status": "ready",
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return dataset_revision_id, target


class FakeAnalysis:
    class Locator:
        def __getitem__(self, key):
            values = {
                ("excess_return_without_cost", "annualized_return"): 0.18,
                ("excess_return_with_cost", "annualized_return"): 0.12,
            }
            return values[key]

    loc = Locator()


class FakeParquetFrame:
    def __init__(self, text: str) -> None:
        self.text = text

    def to_parquet(self, path: Path) -> None:
        path.write_text(self.text, encoding="utf-8")


class QlibExperimentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "qlib"
        self.artifacts_dir = self.root / "artifacts"
        self.data_dir.mkdir()
        self.dataset_dir = write_dataset(self.data_dir)

    def tearDown(self):
        self.temp.cleanup()

    def worker(self, runner=None) -> QlibExperimentWorker:
        return QlibExperimentWorker(self.data_dir, self.artifacts_dir, runner=runner)

    def test_experiment_manager_uses_ephemeral_sqlite_and_restores_environment(self):
        variable = "_MLFLOW_SERVER_ARTIFACT_ROOT"
        original = os.environ.get(variable)
        os.environ[variable] = "file:///existing-artifact-root"
        runtime = None
        try:
            with isolated_experiment_manager(self.artifacts_dir / "experiment") as config:
                uri = config["kwargs"]["uri"]
                self.assertTrue(uri.startswith("sqlite:////"))
                runtime = Path(uri.removeprefix("sqlite:///")).parent
                self.assertTrue(runtime.is_dir())
                self.assertEqual(config["kwargs"]["default_exp_name"], "nexus-quant")
                self.assertTrue(
                    os.environ[variable].startswith(runtime.as_uri())
                )
            self.assertEqual(os.environ[variable], "file:///existing-artifact-root")
        finally:
            if original is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = original

        self.assertIsNotNone(runtime)
        self.assertFalse(runtime.exists())

    def test_controllable_runner_enforces_execution_time_budget(self):
        class FakeQueue:
            @staticmethod
            def close():
                return None

        class FakeProcess:
            def __init__(self):
                self.alive = True

            @staticmethod
            def start():
                return None

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.alive = False

            @staticmethod
            def join(timeout=None):
                del timeout

            def kill(self):
                self.alive = False

        process = FakeProcess()
        context = SimpleNamespace(
            Queue=lambda **_kwargs: FakeQueue(),
            Process=lambda **_kwargs: process,
        )
        runner = QlibRunner(max_execution_seconds=1)
        with (
            patch("worker.adapters.qlib_runner.get_context", return_value=context),
            patch("worker.adapters.qlib_runner.monotonic", side_effect=[0.0, 2.0]),
            self.assertRaises(RuntimeError) as raised,
        ):
            runner.run_controllable(
                REQUEST,
                experiment_id="exp_v1_" + "a" * 32,
                data_dir=self.data_dir,
                artifact_dir=self.artifacts_dir / "timeout",
                cancellation=threading.Event(),
            )

        self.assertEqual(str(raised.exception), "qlib_execution_time_budget_exceeded")
        self.assertEqual(raised.exception.stage, "execution_timeout")
        self.assertFalse(process.is_alive())

    def test_reports_calendar_coverage(self):
        inspection = self.worker().inspect_dataset("openbb-demo", "demo")

        self.assertTrue(inspection.ready)
        self.assertEqual(inspection.start_date, date(2020, 1, 1))
        self.assertEqual(inspection.end_date, date(2020, 1, 20))
        self.assertEqual(inspection.max_test_end, date(2020, 1, 18))
        self.assertEqual(inspection.trading_days, 20)

    def test_manifest_is_verified_before_experiment_submission(self):
        worker = self.worker(runner=lambda *_args, **_kwargs: {})
        dataset_revision_id, dataset_dir = write_manifest_dataset(worker)
        request = ExperimentRequest.model_validate(
            {
                **REQUEST.model_dump(mode="json"),
                "dataset_id": dataset_revision_id,
                "universe": "manifest_demo",
            }
        )

        validation = worker.validate_dataset(dataset_revision_id, "manifest_demo")

        self.assertTrue(validation.valid)
        self.assertTrue(validation.manifest_verified)
        self.assertEqual(validation.dataset_revision_id, dataset_revision_id)
        factor = dataset_dir / "features/aapl/factor.day.bin"
        factor.write_bytes(factor.read_bytes() + b"tampered")
        tampered = worker.validate_dataset(dataset_revision_id, "manifest_demo")
        self.assertFalse(tampered.valid)
        self.assertFalse(tampered.manifest_verified)
        with self.assertRaises(WorkerError) as raised:
            worker.submit_experiment(request)
        self.assertEqual(raised.exception.code, "dataset_not_ready")
        worker.close()

    def test_datasets_are_isolated_between_actors(self):
        actor_one = "v1-" + "1" * 64
        actor_two = "v1-" + "2" * 64
        for actor, dataset_id in (
            (actor_one, "actor-one-data"),
            (actor_two, "actor-two-data"),
        ):
            actor_root = self.data_dir / actor
            actor_root.mkdir()
            write_dataset(actor_root, dataset_id=dataset_id)

        worker = self.worker()
        with actor_context(actor_one):
            actor_one_ids = {item.dataset_id for item in worker.list_datasets()}
        with actor_context(actor_two):
            actor_two_ids = {item.dataset_id for item in worker.list_datasets()}
            with self.assertRaises(WorkerError) as raised:
                worker.validate_dataset("actor-one-data", "demo")

        self.assertEqual(actor_one_ids, {"actor-one-data"})
        self.assertEqual(actor_two_ids, {"actor-two-data"})
        self.assertEqual(raised.exception.code, "unknown_dataset")

    def test_rejects_missing_requested_universe(self):
        (self.dataset_dir / "instruments" / "demo.txt").unlink()

        inspection = self.worker().inspect_dataset("openbb-demo", "demo")

        self.assertFalse(inspection.ready)
        self.assertIn(
            "Missing universe: instruments/demo.txt",
            inspection.errors,
        )

    def test_reports_custom_dataset_and_universe_coverage(self):
        custom_root = self.root / "qlib-root"
        custom_root.mkdir()
        write_custom_dataset(custom_root, "openbb-demo", "demo")
        worker = QlibExperimentWorker(custom_root, self.artifacts_dir)

        inspection = worker.inspect_dataset("openbb-demo", "demo")
        datasets = worker.list_datasets()

        self.assertTrue(inspection.ready)
        self.assertTrue(
            any(
                dataset.dataset_id == "openbb-demo"
                and dataset.universe == "demo"
                and dataset.ready
                for dataset in datasets
            )
        )

    def test_custom_experiment_uses_dataset_subdirectory(self):
        custom_root = self.root / "qlib-root"
        custom_root.mkdir()
        write_custom_dataset(custom_root, "openbb-demo", "demo")
        request = ExperimentRequest.model_validate(
            {
                **REQUEST.model_dump(mode="json"),
                "dataset_id": "openbb-demo",
                "universe": "demo",
            }
        )
        seen = {}

        def runner(request, **kwargs):
            seen["data_dir"] = kwargs["data_dir"]
            return {"dataset": request.dataset_id}

        worker = QlibExperimentWorker(custom_root, self.artifacts_dir, runner=runner)
        accepted = worker.submit_experiment(request)
        for _ in range(100):
            record = worker.load_record(accepted.experiment_id)
            if record.status == JobStatus.COMPLETED:
                break
            time.sleep(0.01)
        worker.close(wait=True)

        self.assertEqual(seen["data_dir"], custom_root / "openbb-demo")
        self.assertEqual(record.result["dataset"], "openbb-demo")

    def test_portfolio_benchmark_uses_custom_universe_symbol(self):
        custom_root = self.root / "qlib-root"
        custom_root.mkdir()
        custom_dataset = write_custom_dataset(custom_root, "openbb-demo", "demo")
        request = ExperimentRequest.model_validate(
            {
                **REQUEST.model_dump(mode="json"),
                "dataset_id": "openbb-demo",
                "universe": "demo",
            }
        )

        config = QlibExperimentWorker(
            custom_root,
            self.artifacts_dir,
        ).runner.portfolio_config(request, data_dir=custom_dataset)

        self.assertEqual(config["backtest"]["benchmark"], "AAPL")
        self.assertEqual(config["backtest"]["exchange_kwargs"]["codes"], "demo")

    def test_list_datasets_returns_empty_when_no_dataset_directories_exist(self):
        root = self.root / "empty-qlib"
        root.mkdir()
        self.assertEqual(QlibExperimentWorker(root, self.artifacts_dir).list_datasets(), [])

    def test_build_model_uses_allowlisted_q_lib_classes(self):
        class FakeModel:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        modules = {
            "qlib.contrib.model.gbdt": SimpleNamespace(LGBModel=FakeModel),
            "qlib.contrib.model.xgboost": SimpleNamespace(XGBModel=FakeModel),
        }

        with patch(
            "worker.adapters.qlib_runner.import_module",
            side_effect=modules.__getitem__,
        ):
            worker = self.worker()
            lightgbm = worker.runner.build_model("lightgbm")
            linear = worker.runner.build_model("linear")
            xgboost = worker.runner.build_model("xgboost")
            catboost = worker.runner.build_model("catboost")

        self.assertEqual(lightgbm.kwargs["loss"], "mse")
        self.assertIsInstance(linear, NaSafeLinearModel)
        self.assertEqual(linear.alpha, 1.0)
        self.assertEqual(xgboost.kwargs["objective"], "reg:squarederror")
        self.assertIsInstance(catboost, CpuCatBoostModel)
        self.assertEqual(catboost.params["task_type"], "CPU")

    def test_na_safe_linear_model_fills_sparse_alpha_features(self):
        class FakeDataset:
            def __init__(self):
                index = pd.MultiIndex.from_product(
                    [[pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02")], ["A", "B"]],
                    names=["datetime", "instrument"],
                )
                features = pd.DataFrame(
                    {
                        "f1": [1.0, None, 3.0, None],
                        "f2": [None, None, 2.0, 4.0],
                    },
                    index=index,
                )
                labels = pd.DataFrame({"LABEL0": [0.1, 0.2, None, 0.4]}, index=index)
                self.train = pd.concat({"feature": features, "label": labels}, axis=1)
                self.test = features

            def prepare(self, segment, col_set=None, data_key=None):
                del data_key
                if segment == "train":
                    return self.train
                if segment == "test" and col_set == "feature":
                    return self.test
                raise KeyError(segment)

        model = NaSafeLinearModel(alpha=1.0)

        model.fit(FakeDataset())
        prediction = model.predict(FakeDataset())

        self.assertEqual(len(prediction), 4)
        self.assertFalse(prediction.isna().any())

    def test_preflight_requires_alpha158_label_lookahead(self):
        request = ExperimentRequest.model_validate(
            {
                **REQUEST.model_dump(mode="json"),
                "test": {"start": "2020-01-11", "end": "2020-01-19"},
            }
        )
        worker = self.worker()

        errors = worker.preflight_experiment(
            request,
            worker.inspect_dataset("openbb-demo", "demo"),
        )

        self.assertTrue(any("2 later trading days" in error for error in errors))
        self.assertTrue(
            any("latest valid test end is 2020-01-18" in error for error in errors)
        )

    def test_rejects_path_traversal_dataset_universe_and_experiment_ids(self):
        worker = self.worker()

        with self.assertRaises(WorkerError) as dataset_error:
            worker.validate_dataset("../secret", "demo")
        with self.assertRaises(WorkerError) as universe_error:
            worker.validate_dataset("openbb-demo", "../secret")
        with self.assertRaises(WorkerError) as experiment_error:
            worker.require_record("../secret")

        self.assertEqual(dataset_error.exception.code, "unknown_dataset")
        self.assertEqual(universe_error.exception.code, "unknown_dataset")
        self.assertEqual(experiment_error.exception.code, "invalid_experiment_id")

    def test_job_reaches_completed_with_fake_runner(self):
        def runner(request, **kwargs):
            return {"signal_metrics": {"ic": 0.03}}

        worker = self.worker(runner=runner)
        accepted = worker.submit_experiment(REQUEST)
        for _ in range(100):
            record = worker.load_record(accepted.experiment_id)
            if record.status == JobStatus.COMPLETED:
                break
            time.sleep(0.01)
        worker.close(wait=True)

        self.assertEqual(record.status, JobStatus.COMPLETED)
        self.assertEqual(record.result["signal_metrics"]["ic"], 0.03)

    def test_completed_artifacts_use_manifests_and_verified_actor_scoped_reads(self):
        def runner(request, **kwargs):
            del request
            artifact_dir = kwargs["artifact_dir"]
            artifact_dir.joinpath("predictions.parquet").write_bytes(b"predictions")
            artifact_dir.joinpath("report.parquet").write_bytes(b"report")
            artifact_dir.joinpath("positions.pkl").write_bytes(b"unsafe-pickle")
            return {"signal_metrics": {"ic": 0.03}}

        worker = self.worker(runner=runner)
        accepted = worker.submit_experiment(REQUEST)
        for _ in range(100):
            record = worker.load_record(accepted.experiment_id)
            if record.status == JobStatus.COMPLETED:
                break
            time.sleep(0.01)

        manifests = record.result["artifacts"]
        prediction = next(
            item for item in manifests if item["artifact_type"] == "predictions"
        )
        positions = next(
            item for item in manifests if item["artifact_type"] == "positions"
        )
        path, verified = worker.artifact_path(prediction["artifact_id"])

        self.assertIsNotNone(prediction["expires_at"])
        self.assertEqual(verified["content_hash"], prediction["content_hash"])
        self.assertEqual(path.read_bytes(), b"predictions")
        self.assertNotIn("storage_key", worker.artifact_manifest(prediction["artifact_id"]))
        with self.assertRaises(WorkerError) as internal:
            worker.artifact_path(positions["artifact_id"])
        self.assertEqual(internal.exception.code, "artifact_not_downloadable")

        path.write_bytes(b"tampered")
        with self.assertRaises(WorkerError) as corrupted:
            worker.artifact_path(prediction["artifact_id"])
        self.assertEqual(corrupted.exception.code, "artifact_corrupted")
        actor = "v1-" + "9" * 64
        with actor_context(actor):
            with self.assertRaises(WorkerError) as foreign:
                worker.artifact_manifest(prediction["artifact_id"])
        self.assertEqual(foreign.exception.code, "unknown_artifact")
        worker.close(wait=True)

    def test_duplicate_active_request_reuses_experiment(self):
        release_runner = threading.Event()

        def runner(request, **kwargs):
            release_runner.wait(timeout=1)
            return {}

        worker = self.worker(runner=runner)
        first = worker.submit_experiment(REQUEST)
        second = worker.submit_experiment(REQUEST)
        release_runner.set()
        worker.close(wait=True)

        self.assertEqual(second.experiment_id, first.experiment_id)
        self.assertIn(second.status, {JobStatus.QUEUED, JobStatus.RUNNING})

    def test_running_cancellation_is_idempotent_and_hides_result(self):
        started = threading.Event()
        release = threading.Event()

        def runner(request, **kwargs):
            del request
            (kwargs["artifact_dir"] / ".partial.tmp").write_text(
                "partial",
                encoding="utf-8",
            )
            started.set()
            release.wait(timeout=1)
            return {"must_not_publish": True}

        worker = self.worker(runner=runner)
        accepted = worker.submit_experiment(REQUEST)
        self.assertTrue(started.wait(timeout=1))

        requested = worker.cancel_experiment(
            accepted.experiment_id,
            reason_category="user_requested",
        )
        repeated = worker.cancel_experiment(
            accepted.experiment_id,
            reason_category="user_requested",
        )
        release.set()
        for _ in range(100):
            record = worker.load_record(accepted.experiment_id)
            if record.status == JobStatus.CANCELLED:
                break
            time.sleep(0.01)
        terminal = worker.cancel_experiment(
            accepted.experiment_id,
            reason_category="user_requested",
        )
        worker.close(wait=True)

        self.assertEqual(requested.status, JobStatus.CANCELLING)
        self.assertEqual(repeated.status, JobStatus.CANCELLING)
        self.assertEqual(record.status, JobStatus.CANCELLED)
        self.assertIsNone(record.result)
        self.assertEqual(terminal.status, JobStatus.CANCELLED)
        self.assertEqual(record.cancellation["resulting_state"], "cancelled")
        self.assertIn("completed_at", record.cancellation)
        self.assertFalse(worker.repository.artifact_dir(accepted.experiment_id).joinpath(".partial.tmp").exists())

    def test_runner_failure_stage_is_persisted(self):
        def runner(request, **kwargs):
            raise QlibStageError("model_fit", RuntimeError("boom"))

        worker = self.worker(runner=runner)
        logging.disable(logging.CRITICAL)
        accepted = worker.submit_experiment(REQUEST)
        try:
            for _ in range(100):
                record = worker.load_record(accepted.experiment_id)
                if record.status == JobStatus.FAILED:
                    break
                time.sleep(0.01)
            worker.close(wait=True)
        finally:
            logging.disable(logging.NOTSET)

        self.assertEqual(record.error.code, "qlib_execution_failed")
        self.assertEqual(record.error.stage, "model_fit")

    def test_runner_failure_message_does_not_expose_raw_exception(self):
        def runner(request, **kwargs):
            raise RuntimeError("x" * 700)

        worker = self.worker(runner=runner)
        logging.disable(logging.CRITICAL)
        accepted = worker.submit_experiment(REQUEST)
        try:
            for _ in range(100):
                record = worker.load_record(accepted.experiment_id)
                if record.status == JobStatus.FAILED:
                    break
                time.sleep(0.01)
            worker.close(wait=True)
        finally:
            logging.disable(logging.NOTSET)

        self.assertEqual(record.error.code, "qlib_execution_failed")
        self.assertEqual(record.error.message, "Qlib execution failed during execution.")
        self.assertNotIn("x" * 20, record.error.message)

    def test_recovery_marks_running_job_failed(self):
        worker = self.worker(runner=lambda *_args, **_kwargs: {})
        accepted = worker.submit_experiment(REQUEST)
        worker.close(wait=True)
        worker.repository.update_record(accepted.experiment_id, status=JobStatus.RUNNING)

        self.assertEqual(worker.recover_interrupted(), 1)
        recovered = worker.load_record(accepted.experiment_id)
        self.assertEqual(recovered.error.code, "worker_interrupted")

    def test_recovery_updates_tenant_scoped_record(self):
        actor = "v1-" + "c" * 64
        worker = self.worker(runner=lambda *_args, **_kwargs: {})
        with actor_context(actor):
            (worker.data_dir / actor).mkdir()
            write_dataset(worker.data_dir / actor)
            accepted = worker.submit_experiment(REQUEST)
            worker.close(wait=True)
            worker.repository.update_record(accepted.experiment_id, status=JobStatus.RUNNING)

        self.assertEqual(worker.recover_interrupted(), 1)
        with actor_context(actor):
            recovered = worker.load_record(accepted.experiment_id)
        self.assertEqual(recovered.error.code, "worker_interrupted")

    def test_writes_dict_positions_as_pickle(self):
        predictions = FakeParquetFrame("predictions")
        report = FakeParquetFrame("report")
        positions = {"2020-01-02": {"cash": 100.0}}
        worker = self.worker()
        artifact_dir = self.artifacts_dir / "manual"

        worker.runner.write_artifacts(predictions, report, positions, artifact_dir)

        self.assertEqual(
            (artifact_dir / "predictions.parquet").read_text(encoding="utf-8"),
            "predictions",
        )
        self.assertEqual(
            (artifact_dir / "report.parquet").read_text(encoding="utf-8"),
            "report",
        )
        self.assertEqual(pd.read_pickle(artifact_dir / "positions.pkl"), positions)
        self.assertEqual(list(artifact_dir.glob(".*.tmp")), [])

    def test_reads_cost_adjusted_portfolio_metric(self):
        self.assertEqual(
            _analysis_metric(FakeAnalysis(), "excess_return_with_cost", "annualized_return"),
            0.12,
        )

    def test_stage_wraps_failure_with_stage_name(self):
        with self.assertRaises(QlibStageError) as raised:
            with stage("portfolio_analysis"):
                raise IndexError("out of bounds")

        self.assertEqual(raised.exception.stage, "portfolio_analysis")
