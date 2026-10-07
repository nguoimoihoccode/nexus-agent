"""Qlib execution adapter and allowlisted model implementations."""

import json
import os
import platform
from contextlib import contextmanager
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from multiprocessing import get_context
from pathlib import Path
from queue import Empty
from threading import Event
from time import monotonic
from tempfile import TemporaryDirectory
from typing import Any

from worker.domain import stage
from worker.models import ExperimentModel, ExperimentRequest


@contextmanager
def isolated_experiment_manager(artifact_dir: Path):
    """Keep Qlib/MLflow tracking state outside published artifact evidence."""
    artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".qlib-runtime-", dir=artifact_dir.parent) as temporary:
        runtime = Path(temporary)
        mlflow_artifacts = runtime / "artifacts"
        mlflow_artifacts.mkdir()
        variable = "_MLFLOW_SERVER_ARTIFACT_ROOT"
        previous = os.environ.get(variable)
        os.environ[variable] = mlflow_artifacts.as_uri()
        try:
            yield {
                "class": "MLflowExpManager",
                "module_path": "qlib.workflow.expm",
                "kwargs": {
                    "uri": f"sqlite:///{runtime / 'tracking.db'}",
                    "default_exp_name": "nexus-quant",
                },
            }
        finally:
            if previous is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = previous


class NaSafeLinearModel:
    """Simple ridge model that tolerates sparse Alpha158 feature matrices."""

    def __init__(self, *, alpha: float = 1.0, fit_intercept: bool = False) -> None:
        self.alpha = alpha
        self.fit_intercept = fit_intercept
        self.model = None
        self.columns = None
        self.fill_values = None

    def fit(self, dataset) -> "NaSafeLinearModel":
        import numpy as np
        import pandas as pd
        from qlib.data.dataset.handler import DataHandlerLP
        from sklearn.linear_model import Ridge

        df_train = dataset.prepare(
            "train",
            col_set=["feature", "label"],
            data_key=DataHandlerLP.DK_L,
        )
        if df_train.empty:
            raise ValueError("Empty data from dataset, please check your dataset config.")

        x_train = df_train["feature"].replace([np.inf, -np.inf], np.nan)
        y_train = df_train["label"]
        if isinstance(y_train, pd.DataFrame):
            if y_train.shape[1] != 1:
                raise ValueError("Linear model does not support multi-label training")
            y_train = y_train.iloc[:, 0]

        valid_label = y_train.notna()
        x_train = x_train.loc[valid_label]
        y_train = y_train.loc[valid_label]
        if x_train.empty:
            raise ValueError("No non-null labels were available for linear model training.")

        self.columns = x_train.columns
        self.fill_values = x_train.median(axis=0).fillna(0.0)
        x_train = x_train.fillna(self.fill_values).fillna(0.0)
        self.model = Ridge(alpha=self.alpha, fit_intercept=self.fit_intercept)
        self.model.fit(x_train.values, y_train.values)
        return self

    def predict(self, dataset, segment="test"):
        import numpy as np
        import pandas as pd
        from qlib.data.dataset.handler import DataHandlerLP

        if self.model is None or self.columns is None or self.fill_values is None:
            raise ValueError("model is not fitted yet!")
        x_test = dataset.prepare(segment, col_set="feature", data_key=DataHandlerLP.DK_I)
        x_test = x_test.reindex(columns=self.columns).replace(
            [np.inf, -np.inf],
            np.nan,
        )
        x_test = x_test.fillna(self.fill_values).fillna(0.0)
        return pd.Series(self.model.predict(x_test.values), index=x_test.index)


class CpuCatBoostModel:
    """CatBoost regression model pinned to CPU for local worker stability."""

    def __init__(
        self,
        *,
        loss: str = "RMSE",
        learning_rate: float = 0.2,
        depth: int = 8,
        thread_count: int = 4,
        random_seed: int = 42,
    ) -> None:
        self.params = {
            "loss_function": loss,
            "learning_rate": learning_rate,
            "depth": depth,
            "thread_count": thread_count,
            "random_seed": random_seed,
            "task_type": "CPU",
        }
        self.model = None

    def fit(
        self,
        dataset,
        num_boost_round: int = 1000,
        early_stopping_rounds: int = 50,
        verbose_eval: int = 20,
        **kwargs,
    ) -> "CpuCatBoostModel":
        import numpy as np
        import pandas as pd
        from catboost import CatBoost, Pool
        from qlib.data.dataset.handler import DataHandlerLP

        df_train, df_valid = dataset.prepare(
            ["train", "valid"],
            col_set=["feature", "label"],
            data_key=DataHandlerLP.DK_L,
        )
        if df_train.empty or df_valid.empty:
            raise ValueError("Empty data from dataset, please check your dataset config.")

        x_train, y_train = df_train["feature"], df_train["label"]
        x_valid, y_valid = df_valid["feature"], df_valid["label"]
        if isinstance(y_train, pd.DataFrame):
            if y_train.shape[1] != 1:
                raise ValueError("CatBoost does not support multi-label training")
            y_train = y_train.iloc[:, 0]
            y_valid = y_valid.iloc[:, 0]

        train_mask = y_train.notna()
        valid_mask = y_valid.notna()
        x_train = x_train.loc[train_mask].replace([np.inf, -np.inf], np.nan)
        y_train = y_train.loc[train_mask]
        x_valid = x_valid.loc[valid_mask].replace([np.inf, -np.inf], np.nan)
        y_valid = y_valid.loc[valid_mask]
        if x_train.empty or x_valid.empty:
            raise ValueError("No non-null labels were available for CatBoost training.")

        params = {
            **self.params,
            "iterations": num_boost_round,
            "early_stopping_rounds": early_stopping_rounds,
            "verbose": verbose_eval,
        }
        self.model = CatBoost(params, **kwargs)
        self.model.fit(
            Pool(data=x_train, label=y_train),
            eval_set=Pool(data=x_valid, label=y_valid),
            use_best_model=True,
        )
        return self

    def predict(self, dataset, segment="test"):
        import numpy as np
        import pandas as pd
        from qlib.data.dataset.handler import DataHandlerLP

        if self.model is None:
            raise ValueError("model is not fitted yet!")
        x_test = dataset.prepare(segment, col_set="feature", data_key=DataHandlerLP.DK_I)
        x_test = x_test.replace([np.inf, -np.inf], np.nan)
        return pd.Series(self.model.predict(x_test), index=x_test.index)


class QlibRunner:
    """Execute one allowlisted Qlib experiment and persist its artifacts."""

    def __init__(self, *, max_execution_seconds: int = 3_600) -> None:
        if max_execution_seconds < 1:
            raise ValueError("max_execution_seconds must be positive")
        self.max_execution_seconds = max_execution_seconds

    def __call__(
        self,
        request: ExperimentRequest,
        *,
        experiment_id: str,
        data_dir: Path,
        artifact_dir: Path,
    ) -> dict[str, Any]:
        with isolated_experiment_manager(artifact_dir) as exp_manager:
            with stage("setup"):
                import qlib
                from qlib.contrib.data.handler import Alpha158
                from qlib.data.dataset import DatasetH
                from qlib.workflow import R
                from qlib.workflow.record_temp import (
                    PortAnaRecord,
                    SigAnaRecord,
                    SignalRecord,
                )

                qlib.init(provider_uri=str(data_dir), exp_manager=exp_manager)
                handler = Alpha158(
                    start_time=str(request.train.start),
                    end_time=str(request.test.end),
                    fit_start_time=str(request.train.start),
                    fit_end_time=str(request.train.end),
                    instruments=request.universe,
                )
                dataset = DatasetH(
                    handler=handler,
                    segments={
                        "train": (str(request.train.start), str(request.train.end)),
                        "valid": (str(request.valid.start), str(request.valid.end)),
                        "test": (str(request.test.start), str(request.test.end)),
                    },
                )
                model = self.build_model(request.model)

            portfolio_config = self.portfolio_config(request, data_dir=data_dir)
            with stage("recording"):
                with R.start(experiment_name="nexus-quant", recorder_name=experiment_id):
                    with stage("model_fit"):
                        model.fit(dataset)
                    recorder = R.get_recorder()
                    with stage("signal_analysis"):
                        SignalRecord(model, dataset, recorder).generate()
                        SigAnaRecord(recorder).generate()
                    with stage("portfolio_analysis"):
                        PortAnaRecord(recorder, portfolio_config, "day").generate()

                    with stage("result_loading"):
                        predictions = recorder.load_object("pred.pkl")
                        ic = recorder.load_object("sig_analysis/ic.pkl")
                        ric = recorder.load_object("sig_analysis/ric.pkl")
                        report = recorder.load_object(
                            "portfolio_analysis/report_normal_1day.pkl"
                        )
                        positions = recorder.load_object(
                            "portfolio_analysis/positions_normal_1day.pkl"
                        )
                        analysis = recorder.load_object(
                            "portfolio_analysis/port_analysis_1day.pkl"
                        )

        with stage("artifact_write"):
            self.write_artifacts(predictions, report, positions, artifact_dir)

        result = self.normalize_result(
            request,
            experiment_id,
            ic=ic,
            ric=ric,
            report=report,
            analysis=analysis,
        )
        (artifact_dir / "result.json").write_text(
            json.dumps(result, indent=2, default=str),
            encoding="utf-8",
        )
        return result

    def run_controllable(
        self,
        request: ExperimentRequest,
        *,
        experiment_id: str,
        data_dir: Path,
        artifact_dir: Path,
        cancellation: Event,
    ) -> dict[str, Any]:
        """Run Qlib outside the coordinator process with a cancellation boundary."""
        context = get_context("spawn")
        queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_run_qlib_child,
            args=(
                request.model_dump(mode="json"),
                experiment_id,
                str(data_dir),
                str(artifact_dir),
                queue,
            ),
            daemon=False,
            name=f"qlib-{experiment_id}",
        )
        process.start()
        deadline = monotonic() + self.max_execution_seconds
        try:
            while process.is_alive():
                if cancellation.wait(0.2):
                    process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=2)
                    raise RuntimeError("qlib_execution_cancelled")
                if monotonic() >= deadline:
                    process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=2)
                    error = RuntimeError("qlib_execution_time_budget_exceeded")
                    error.stage = "execution_timeout"  # type: ignore[attr-defined]
                    raise error
            process.join(timeout=1)
            try:
                outcome = queue.get(timeout=1)
            except Empty as exc:
                raise RuntimeError("qlib_child_exited_without_result") from exc
            if outcome.get("ok") is True:
                return outcome["result"]
            error = RuntimeError("qlib_child_execution_failed")
            error.stage = outcome.get("stage") or "execution"  # type: ignore[attr-defined]
            raise error
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            queue.close()

    @staticmethod
    def runtime_versions(request: ExperimentRequest) -> dict[str, str]:
        versions = {
            "python": platform.python_version(),
            "model_preset": request.model,
            "feature_set": request.feature_set,
            "strategy": request.strategy.type,
            "worker_contract": "1",
        }
        for distribution in ("pyqlib", "pandas", "numpy"):
            try:
                versions[distribution] = version(distribution)
            except PackageNotFoundError:
                versions[distribution] = "unavailable"
        return versions

    def build_model(self, model_name: ExperimentModel) -> Any:
        """Build an allowlisted Qlib model with stable benchmark presets."""
        if model_name == "lightgbm":
            module = import_module("qlib.contrib.model.gbdt")
            return module.LGBModel(
                loss="mse",
                learning_rate=0.2,
                max_depth=8,
                num_leaves=210,
                num_threads=4,
                colsample_bytree=0.8879,
                subsample=0.8789,
                lambda_l1=205.6999,
                lambda_l2=580.9768,
            )
        if model_name == "linear":
            return NaSafeLinearModel(alpha=1.0, fit_intercept=False)
        if model_name == "xgboost":
            module = import_module("qlib.contrib.model.xgboost")
            return module.XGBModel(
                objective="reg:squarederror",
                eta=0.2,
                max_depth=8,
                nthread=4,
                colsample_bytree=0.8879,
                subsample=0.8789,
            )
        if model_name == "catboost":
            return CpuCatBoostModel(
                loss="RMSE",
                learning_rate=0.2,
                depth=8,
                thread_count=4,
                random_seed=42,
            )
        raise ValueError(f"Unsupported model: {model_name}")

    def portfolio_config(
        self,
        request: ExperimentRequest,
        *,
        data_dir: Path,
    ) -> dict[str, Any]:
        strategy = request.strategy
        return {
            "executor": {
                "class": "SimulatorExecutor",
                "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
            },
            "strategy": {
                "class": "TopkDropoutStrategy",
                "module_path": "qlib.contrib.strategy.signal_strategy",
                "kwargs": {
                    "signal": "<PRED>",
                    "topk": strategy.topk,
                    "n_drop": strategy.n_drop,
                },
            },
            "backtest": {
                "start_time": str(request.test.start),
                "end_time": str(request.test.end),
                "account": strategy.account,
                "benchmark": self.benchmark_symbol(request, data_dir),
                "exchange_kwargs": {
                    "freq": "day",
                    "codes": request.universe,
                    "limit_threshold": 0.095,
                    "deal_price": "close",
                    "open_cost": strategy.open_cost,
                    "close_cost": strategy.close_cost,
                    "min_cost": strategy.min_cost,
                },
            },
        }

    def benchmark_symbol(self, request: ExperimentRequest, data_dir: Path) -> str | None:
        instrument_path = data_dir / "instruments" / f"{request.universe.lower()}.txt"
        symbols = _instrument_symbols(instrument_path)
        return symbols[0] if symbols else None

    def normalize_result(
        self,
        request: ExperimentRequest,
        experiment_id: str,
        *,
        ic: Any,
        ric: Any,
        report: Any,
        analysis: Any,
    ) -> dict[str, Any]:
        return {
            "dataset": {
                "id": request.dataset_id,
                "universe": request.universe,
                "frequency": "day",
            },
            "model": request.model,
            "feature_set": request.feature_set,
            "strategy": request.strategy.model_dump(),
            "segments": {
                "train": request.train.model_dump(mode="json"),
                "valid": request.valid.model_dump(mode="json"),
                "test": request.test.model_dump(mode="json"),
            },
            "signal_metrics": {
                "ic": _scalar(ic.mean()),
                "icir": _ratio(ic.mean(), ic.std()),
                "rank_ic": _scalar(ric.mean()),
                "rank_icir": _ratio(ric.mean(), ric.std()),
            },
            "portfolio_metrics": {
                "annualized_return": _annualized_return(report["return"]),
                "excess_return_after_cost": _analysis_metric(
                    analysis,
                    "excess_return_with_cost",
                    "annualized_return",
                ),
                "information_ratio": _analysis_metric(
                    analysis,
                    "excess_return_with_cost",
                    "information_ratio",
                ),
                "max_drawdown": _analysis_metric(
                    analysis,
                    "excess_return_with_cost",
                    "max_drawdown",
                ),
                "turnover": (
                    _scalar(report["turnover"].mean()) if "turnover" in report else None
                ),
            },
            "artifacts": [
                {
                    "type": "predictions",
                    "filename": "predictions.parquet",
                },
                {
                    "type": "report",
                    "filename": "report.parquet",
                },
                {
                    "type": "positions",
                    "filename": "positions.pkl",
                },
            ],
            "warnings": [
                "Historical backtest only, not investment advice.",
                "Review leakage, survivorship bias, point-in-time membership, and transaction-cost assumptions.",
            ],
        }

    @staticmethod
    def write_artifacts(
        predictions: Any,
        report: Any,
        positions: Any,
        artifact_dir: Path,
    ) -> None:
        import pandas as pd

        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts = (
            ("predictions.parquet", lambda path: predictions.to_parquet(path)),
            ("report.parquet", lambda path: report.to_parquet(path)),
            ("positions.pkl", lambda path: pd.to_pickle(positions, path)),
        )
        temporary_paths: list[Path] = []
        try:
            for filename, writer in artifacts:
                destination = artifact_dir / filename
                temporary = artifact_dir / f".{filename}.tmp"
                temporary_paths.append(temporary)
                writer(temporary)
                os.replace(temporary, destination)
        finally:
            for temporary in temporary_paths:
                temporary.unlink(missing_ok=True)


def _run_qlib_child(
    request_payload: dict[str, Any],
    experiment_id: str,
    data_dir: str,
    artifact_dir: str,
    queue: Any,
) -> None:
    try:
        result = QlibRunner()(
            ExperimentRequest.model_validate(request_payload),
            experiment_id=experiment_id,
            data_dir=Path(data_dir),
            artifact_dir=Path(artifact_dir),
        )
    except Exception as exc:
        queue.put(
            {
                "ok": False,
                "stage": getattr(exc, "stage", None) or "execution",
                "error_type": type(exc).__name__,
            }
        )
    else:
        queue.put({"ok": True, "result": result})


def _instrument_symbols(instrument_path: Path) -> list[str]:
    if not instrument_path.is_file():
        return []
    try:
        lines = instrument_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [parts[0] for line in lines if len(parts := line.split("\t")) >= 3 and parts[0]]


def _scalar(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: Any, denominator: Any) -> float | None:
    bottom = _scalar(denominator)
    if not bottom:
        return None
    top = _scalar(numerator)
    return None if top is None else top / bottom


def _analysis_metric(value: Any, section: str, key: str) -> float | None:
    try:
        selected = value.loc[(section, key)]
        return _scalar(selected.iloc[0] if hasattr(selected, "iloc") else selected)
    except (KeyError, TypeError, AttributeError):
        return None


def _annualized_return(returns: Any, periods: int = 252) -> float | None:
    try:
        clean = returns.dropna()
        if clean.empty:
            return None
        return _scalar((1 + clean).prod() ** (periods / len(clean)) - 1)
    except (TypeError, ValueError, AttributeError):
        return None
