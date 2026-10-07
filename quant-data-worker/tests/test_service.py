import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from pydantic import ValidationError

from worker.domain import (
    STAGING_SCHEMA,
    WorkerError,
    action_fingerprint,
    canonical_json_bytes,
    content_hash,
    request_fingerprint,
    revision_id,
)
from worker.adapters import MarketDataStore, OpenBBFetcher
from worker.models import (
    BuildQlibDatasetRequest,
    FactorSnapshotRequest,
    FetchOhlcvRequest,
    MarketDataLimitations,
)
from worker.security import actor_context
from worker.service import OpenBBDataWorker


class FakeFetcher:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def fetch_ohlcv(self, request: FetchOhlcvRequest) -> pd.DataFrame:
        return self.frame


class SequenceFetcher:
    def __init__(self, frames: list[pd.DataFrame]) -> None:
        self.frames = iter(frames)

    def fetch_ohlcv(self, request: FetchOhlcvRequest) -> pd.DataFrame:
        del request
        return next(self.frames)


class FakeFactorFetcher:
    def __init__(
        self,
        frames: dict[str, pd.DataFrame] | None = None,
        failures: set[str] | None = None,
    ) -> None:
        self.frames = frames or {}
        self.failures = failures or set()

    def fetch_factor_snapshot(self, symbol: str, provider: str) -> pd.DataFrame:
        del provider
        if symbol in self.failures:
            raise WorkerError(
                "openbb_factor_fetch_failed",
                f"OpenBB factor fetch failed for {symbol}: unavailable",
                stage="factor_snapshot",
            )
        return self.frames.get(symbol, pd.DataFrame())


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": "AAPL", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000},
            {"date": "2024-01-03", "symbol": "AAPL", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1100},
            {"date": "2024-01-02", "symbol": "MSFT", "open": 200, "high": 202, "low": 199, "close": 201, "volume": 2000},
            {"date": "2024-01-03", "symbol": "MSFT", "open": 201, "high": 203, "low": 200, "close": 202, "volume": 2100},
        ]
    )


class QuantDataWorkerTests(unittest.TestCase):
    def make_worker(self, root: Path, frame: pd.DataFrame) -> OpenBBDataWorker:
        store = MarketDataStore(root / "staging", root / "qlib")
        return OpenBBDataWorker(store, FakeFetcher(frame))

    def test_fetch_stages_normalized_openbb_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = self.make_worker(Path(temporary), sample_frame())
            request = FetchOhlcvRequest.model_validate(
                {
                    "symbols": ["aapl", "MSFT"],
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-05",
                    "provider": "yfinance",
                    "adjustment": "auto",
                }
            )

            response = worker.fetch_openbb_ohlcv(request)

            self.assertEqual(response.symbols_loaded, 2)
            self.assertEqual(response.rows, 4)
            self.assertEqual(response.source.adjustment, "splits_and_dividends")
            self.assertEqual(response.limitations.production_eligibility, "research_only")
            self.assertEqual(
                response.limitations.provider_evidence.provider_name,
                "yfinance",
            )
            self.assertEqual(
                response.limitations.market_session.calendar_source,
                "returned_rows_union",
            )
            self.assertFalse(
                response.limitations.fundamental_availability.historical_training_eligible
            )
            self.assertTrue((Path(temporary) / "staging" / response.dataset_staging_id / "data.jsonl").is_file())

    def test_factor_snapshot_request_normalizes_symbols(self):
        request = FactorSnapshotRequest.model_validate(
            {
                "symbols": ["aapl", "MSFT"],
                "as_of_date": "2026-07-08",
                "provider": "yfinance",
            }
        )

        self.assertEqual(request.symbols, ["AAPL", "MSFT"])

    def test_factor_snapshot_request_rejects_duplicates(self):
        with self.assertRaises(ValidationError):
            FactorSnapshotRequest.model_validate(
                {
                    "symbols": ["AAPL", "aapl"],
                    "as_of_date": "2026-07-08",
                    "provider": "yfinance",
                }
            )

    def test_factor_snapshot_request_rejects_unknown_provider(self):
        with self.assertRaises(ValidationError):
            FactorSnapshotRequest.model_validate(
                {
                    "symbols": ["AAPL"],
                    "as_of_date": "2026-07-08",
                    "provider": "other",
                }
            )

    def test_fetch_factor_snapshot_normalizes_and_saves_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = OpenBBDataWorker(
                MarketDataStore(root / "staging", root / "qlib"),
                FakeFactorFetcher(
                    {
                        "AAPL": pd.DataFrame(
                            [
                                {
                                    "symbol": "AAPL",
                                    "market_cap": 1_000_000,
                                    "trailing_pe": 30.5,
                                    "price_to_book": 12.4,
                                    "debt_to_equity": 1.2,
                                    "roe": 0.42,
                                    "revenue_growth_yoy": 0.08,
                                    "gross_margin": 0.44,
                                    "operating_margin": 0.31,
                                    "profit_margin": 0.25,
                                    "currency": "USD",
                                    "period_end_date": "2026-03-31",
                                }
                            ]
                        )
                    }
                ),
            )
            request = FactorSnapshotRequest.model_validate(
                {
                    "symbols": ["AAPL"],
                    "as_of_date": "2026-07-08",
                    "provider": "yfinance",
                }
            )

            response = worker.fetch_factor_snapshot(request)
            snapshot_dir = root / "staging" / "factors" / response.factor_snapshot_id

            self.assertEqual(response.symbols_loaded, 1)
            self.assertEqual(response.factors[0].symbol, "AAPL")
            self.assertEqual(response.factors[0].pe_ratio, 30.5)
            self.assertEqual(response.factors[0].return_on_equity, 0.42)
            self.assertEqual(str(response.factors[0].period_ending), "2026-03-31")
            self.assertFalse(response.limitations.historical_training_eligible)
            self.assertEqual(response.limitations.availability_dates, "unsupported")
            self.assertEqual(
                response.limitations.provider_evidence.provider_name,
                "yfinance",
            )
            self.assertTrue((snapshot_dir / "data.jsonl").is_file())
            self.assertTrue((snapshot_dir / "metadata.json").is_file())

    def test_fetch_factor_snapshot_keeps_partial_successes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = OpenBBDataWorker(
                MarketDataStore(root / "staging", root / "qlib"),
                FakeFactorFetcher(
                    {"AAPL": pd.DataFrame([{"symbol": "AAPL", "market_cap": 1_000_000}])},
                    failures={"MSFT"},
                ),
            )

            response = worker.fetch_factor_snapshot(
                FactorSnapshotRequest.model_validate(
                    {
                        "symbols": ["AAPL", "MSFT"],
                        "as_of_date": "2026-07-08",
                        "provider": "yfinance",
                    }
                )
            )

            self.assertEqual(response.symbols_loaded, 1)
            self.assertEqual(response.factors[0].symbol, "AAPL")
            self.assertTrue(any("MSFT" in warning for warning in response.warnings))

    def test_fetch_factor_snapshot_fails_when_all_symbols_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = OpenBBDataWorker(
                MarketDataStore(root / "staging", root / "qlib"),
                FakeFactorFetcher(failures={"AAPL"}),
            )

            with self.assertRaises(WorkerError) as raised:
                worker.fetch_factor_snapshot(
                    FactorSnapshotRequest.model_validate(
                        {
                            "symbols": ["AAPL"],
                            "as_of_date": "2026-07-08",
                            "provider": "yfinance",
                        }
                    )
                )

            self.assertEqual(raised.exception.code, "no_factor_data")

    def test_fetch_ohlcv_request_rejects_duplicate_blank_and_oversized_symbols(self):
        duplicate = {
            "symbols": ["AAPL", "aapl"],
            "start_date": "2024-01-01",
            "end_date": "2024-01-05",
            "provider": "yfinance",
        }
        blank = {**duplicate, "symbols": ["AAPL", " "]}
        oversized = {**duplicate, "symbols": [f"S{i}" for i in range(301)]}

        for payload in (duplicate, blank, oversized):
            with self.subTest(symbols=len(payload["symbols"])):
                with self.assertRaises(ValidationError):
                    FetchOhlcvRequest.model_validate(payload)

    def test_fetch_ohlcv_request_rejects_inverted_date_range(self):
        with self.assertRaises(ValidationError):
            FetchOhlcvRequest.model_validate(
                {
                    "symbols": ["AAPL"],
                    "start_date": "2024-01-05",
                    "end_date": "2024-01-01",
                    "provider": "yfinance",
                }
            )

    def test_fetch_uses_stable_staging_id_for_same_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = self.make_worker(Path(temporary), sample_frame())
            first = worker.fetch_openbb_ohlcv(
                FetchOhlcvRequest.model_validate(
                    {
                        "symbols": ["AAPL", "MSFT"],
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-05",
                        "provider": "yfinance",
                    }
                )
            )
            second = worker.fetch_openbb_ohlcv(
                FetchOhlcvRequest.model_validate(
                    {
                        "symbols": ["MSFT", "AAPL"],
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-05",
                        "provider": "yfinance",
                    }
                )
            )

            self.assertEqual(second.dataset_staging_id, first.dataset_staging_id)
            self.assertEqual(second.staging_revision_id, first.staging_revision_id)
            self.assertEqual(second.request_fingerprint, first.request_fingerprint)
            self.assertEqual(second.content_hash, first.content_hash)

    def test_same_request_with_changed_content_creates_a_new_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            changed = sample_frame().copy()
            changed["close"] = changed["close"].astype(float)
            changed.loc[0, "close"] = 101.5
            worker = OpenBBDataWorker(
                MarketDataStore(root / "staging", root / "qlib"),
                SequenceFetcher([sample_frame(), changed]),
            )
            request = FetchOhlcvRequest.model_validate(
                {
                    "symbols": ["AAPL", "MSFT"],
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-05",
                    "provider": "yfinance",
                }
            )

            first = worker.fetch_openbb_ohlcv(request)
            second = worker.fetch_openbb_ohlcv(request)

            self.assertEqual(second.request_fingerprint, first.request_fingerprint)
            self.assertNotEqual(second.content_hash, first.content_hash)
            self.assertNotEqual(second.staging_revision_id, first.staging_revision_id)
            self.assertTrue((root / "staging" / first.staging_revision_id).is_dir())
            self.assertTrue((root / "staging" / second.staging_revision_id).is_dir())
            links = list(
                (
                    root
                    / "staging"
                    / "_requests"
                    / "openbb_ohlcv"
                    / first.request_fingerprint
                ).glob("*.json")
            )
            self.assertEqual(len(links), 2)

    def test_staging_revision_metadata_is_versioned_and_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = self.make_worker(root, sample_frame())
            request = FetchOhlcvRequest.model_validate(
                {
                    "symbols": ["AAPL", "MSFT"],
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-05",
                    "provider": "yfinance",
                }
            )

            first = worker.fetch_openbb_ohlcv(request)
            metadata_path = root / "staging" / first.staging_revision_id / "metadata.json"
            original = metadata_path.read_bytes()
            second = worker.fetch_openbb_ohlcv(request)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

            self.assertEqual(second.staging_revision_id, first.staging_revision_id)
            self.assertEqual(metadata_path.read_bytes(), original)
            self.assertEqual(metadata["schema_version"], "1")
            self.assertEqual(metadata["request_fingerprint"], first.request_fingerprint)
            self.assertEqual(metadata["content_hash"], first.content_hash)
            self.assertEqual(metadata["row_count"], 4)
            self.assertEqual(metadata["source"]["requested_adjustment"], "auto")
            self.assertEqual(metadata["request_parameters"]["symbols"], ["AAPL", "MSFT"])
            self.assertEqual(
                metadata["limitations"]["symbol_semantics"]["delisting_support"],
                "unsupported",
            )
            self.assertEqual(
                metadata["source"]["effective_adjustment"],
                "splits_and_dividends",
            )

    def test_unqualified_limitations_cannot_be_upgraded(self):
        payload = {
            "point_in_time_status": "unsupported",
            "survivorship_status": "user_supplied",
            "adjustment_status": {
                "requested_policy": "auto",
                "effective_policy": "splits_and_dividends",
                "verification_status": "unsupported",
            },
            "calendar_status": "inferred_union",
            "missing_data_policy": "no_fill_per_symbol",
            "provider_revision_status": "unsupported",
            "production_eligibility": "eligible",
        }

        with self.assertRaises(ValidationError):
            MarketDataLimitations.model_validate(payload)

    def test_canonical_identity_matches_cross_service_golden_vector(self):
        fixture_path = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "reference"
            / "canonical-identity-v1.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        request_vector = fixture["request_vector"]
        action_vector = fixture["action_vector"]
        content_vector = fixture["content_vector"]

        self.assertEqual(
            canonical_json_bytes(request_vector["payload"]).decode("utf-8"),
            request_vector["canonical_json"],
        )
        self.assertEqual(
            request_fingerprint(request_vector["namespace"], request_vector["payload"]),
            request_vector["request_fingerprint"],
        )
        self.assertEqual(
            action_fingerprint(action_vector["payload"]),
            action_vector["action_fingerprint"],
        )
        digest = content_hash(content_vector["namespace"], content_vector["payload"])
        self.assertEqual(digest, content_vector["content_hash"])
        self.assertEqual(
            revision_id("stg", digest),
            content_vector["staging_revision_id"],
        )

    def test_openbb_preload_converts_initialization_failure_to_worker_error(self):
        with patch(
            "worker.adapters.openbb.importlib.import_module",
            side_effect=ValueError("signal only works in main thread"),
        ):
            fetcher = OpenBBFetcher.preload()

        with self.assertRaises(WorkerError) as raised:
            fetcher.fetch_ohlcv(
                FetchOhlcvRequest.model_validate(
                    {
                        "symbols": ["AAPL"],
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-05",
                        "provider": "yfinance",
                    }
                )
            )

        self.assertEqual(raised.exception.code, "openbb_unavailable")
        self.assertIn("OpenBB failed to initialize", raised.exception.message)

    def test_market_validation_rejects_duplicates_and_bad_ohlc(self):
        with tempfile.TemporaryDirectory() as temporary:
            bad = pd.concat([sample_frame().iloc[[0]], sample_frame().iloc[[0]]])
            bad.loc[bad.index[0], "high"] = 90
            worker = self.make_worker(Path(temporary), bad)
            request = FetchOhlcvRequest.model_validate(
                {
                    "symbols": ["AAPL"],
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-05",
                    "provider": "yfinance",
                }
            )
            staged = worker.fetch_openbb_ohlcv(request)

            validation = worker.validate_market_data(staged.dataset_staging_id)

            self.assertFalse(validation.valid)
            self.assertEqual(validation.quality_checks["duplicate_rows"], 1)
            self.assertEqual(validation.quality_checks["ohlc_consistency_errors"], 2)

    def test_builds_and_validates_minimal_qlib_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = self.make_worker(root, sample_frame())
            staged = worker.fetch_openbb_ohlcv(
                FetchOhlcvRequest.model_validate(
                    {
                        "symbols": ["AAPL", "MSFT"],
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-05",
                        "provider": "yfinance",
                        "adjustment": "adjusted",
                    }
                )
            )

            build = worker.build_qlib_dataset(
                BuildQlibDatasetRequest.model_validate(
                    {
                        "dataset_staging_id": staged.dataset_staging_id,
                        "dataset_alias": "openbb-us-equity-daily-test",
                        "universe_name": "custom_us_2",
                    }
                )
            )
            validation = worker.validate_qlib_dataset(build.dataset_id)
            close_path = root / "qlib" / build.dataset_id / "features" / "aapl" / "close.day.bin"
            close_values = np.fromfile(close_path, dtype="<f")

            self.assertTrue(validation.valid)
            self.assertEqual(validation.universes, ["custom_us_2"])
            self.assertEqual(close_values[0], 0.0)
            self.assertEqual(close_values[1:].tolist(), [101.0, 102.0])

    def test_store_rejects_path_traversal_identifiers(self):
        store = MarketDataStore(Path("/tmp/staging"), Path("/tmp/qlib"))

        cases = (
            (store.staging_path, "../secret", "invalid_staging_id"),
            (store.staging_path, "stg_../../secret", "invalid_staging_id"),
            (store.dataset_path, "../secret", "invalid_dataset_id"),
            (store.dataset_path, "openbb/secret", "invalid_dataset_id"),
            (store.factor_snapshot_path, "fac_../../secret", "invalid_factor_snapshot_id"),
        )
        for resolver, value, code in cases:
            with self.subTest(value=value):
                with self.assertRaises(WorkerError) as raised:
                    resolver(value)
                self.assertEqual(raised.exception.code, code)

    def test_identical_dataset_build_reuses_immutable_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = self.make_worker(root, sample_frame())
            staged = worker.fetch_openbb_ohlcv(
                FetchOhlcvRequest.model_validate(
                    {
                        "symbols": ["AAPL", "MSFT"],
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-05",
                        "provider": "yfinance",
                    }
                )
            )
            request = BuildQlibDatasetRequest.model_validate(
                {
                    "dataset_staging_id": staged.dataset_staging_id,
                    "dataset_alias": "openbb-us-equity-daily-test",
                    "universe_name": "custom_us_2",
                }
            )

            first = worker.build_qlib_dataset(request)
            manifest_path = root / "qlib" / first.dataset_revision_id / "manifest.json"
            original = manifest_path.read_bytes()
            second = worker.build_qlib_dataset(request)

            self.assertEqual(second.dataset_revision_id, first.dataset_revision_id)
            self.assertEqual(second.manifest_hash, first.manifest_hash)
            self.assertEqual(manifest_path.read_bytes(), original)
            self.assertEqual(
                list((root / "qlib").glob(".tmp_dataset_build_*")),
                [],
            )

    def test_dataset_alias_does_not_change_content_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = self.make_worker(root, sample_frame())
            staged = worker.fetch_openbb_ohlcv(
                FetchOhlcvRequest.model_validate(
                    {
                        "symbols": ["AAPL", "MSFT"],
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-05",
                        "provider": "yfinance",
                    }
                )
            )
            common = {
                "dataset_staging_id": staged.staging_revision_id,
                "universe_name": "custom_us_2",
            }

            first = worker.build_qlib_dataset(
                BuildQlibDatasetRequest.model_validate(
                    {**common, "dataset_alias": "research-alias-one"}
                )
            )
            second = worker.build_qlib_dataset(
                BuildQlibDatasetRequest.model_validate(
                    {**common, "dataset_alias": "research-alias-two"}
                )
            )

            self.assertEqual(second.dataset_revision_id, first.dataset_revision_id)
            self.assertTrue((root / "qlib/_aliases/research-alias-one.json").is_file())
            self.assertTrue((root / "qlib/_aliases/research-alias-two.json").is_file())

    def test_user_dataset_id_is_only_a_legacy_alias_not_canonical_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = self.make_worker(root, sample_frame())
            staged = worker.fetch_openbb_ohlcv(
                FetchOhlcvRequest.model_validate(
                    {
                        "symbols": ["AAPL", "MSFT"],
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-05",
                        "provider": "yfinance",
                    }
                )
            )
            requested_id = "dsr_v1_000000000000000000000000"

            build = worker.build_qlib_dataset(
                BuildQlibDatasetRequest.model_validate(
                    {
                        "dataset_staging_id": staged.staging_revision_id,
                        "dataset_id": requested_id,
                        "universe_name": "custom_us_2",
                    }
                )
            )

            self.assertEqual(build.dataset_alias, requested_id)
            self.assertNotEqual(build.dataset_revision_id, requested_id)

    def test_dataset_manifest_tampering_is_detected_and_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = self.make_worker(root, sample_frame())
            staged = worker.fetch_openbb_ohlcv(
                FetchOhlcvRequest.model_validate(
                    {
                        "symbols": ["AAPL", "MSFT"],
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-05",
                        "provider": "yfinance",
                    }
                )
            )
            request = BuildQlibDatasetRequest.model_validate(
                {
                    "dataset_staging_id": staged.staging_revision_id,
                    "dataset_alias": "tamper-test",
                    "universe_name": "custom_us_2",
                }
            )
            build = worker.build_qlib_dataset(request)
            close_path = (
                root
                / "qlib"
                / build.dataset_revision_id
                / "features/aapl/close.day.bin"
            )
            close_path.write_bytes(close_path.read_bytes() + b"tampered")

            validation = worker.validate_qlib_dataset(build.dataset_revision_id)
            self.assertFalse(validation.valid)
            self.assertFalse(validation.manifest_verified)
            self.assertTrue(any("manifest" in error.lower() for error in validation.errors))
            with self.assertRaises(WorkerError) as raised:
                worker.build_qlib_dataset(request)
            self.assertEqual(raised.exception.code, "invalid_immutable_dataset")

    def test_dataset_revision_is_not_visible_to_another_actor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = self.make_worker(root, sample_frame())
            actor_one = "v1-" + "1" * 64
            actor_two = "v1-" + "2" * 64
            with actor_context(actor_one):
                staged = worker.fetch_openbb_ohlcv(
                    FetchOhlcvRequest.model_validate(
                        {
                            "symbols": ["AAPL", "MSFT"],
                            "start_date": "2024-01-01",
                            "end_date": "2024-01-05",
                            "provider": "yfinance",
                        }
                    )
                )
                build = worker.build_qlib_dataset(
                    BuildQlibDatasetRequest.model_validate(
                        {
                            "dataset_staging_id": staged.staging_revision_id,
                            "dataset_alias": "actor-one-dataset",
                            "universe_name": "custom_us_2",
                        }
                    )
                )

            with actor_context(actor_two):
                with self.assertRaises(WorkerError) as raised:
                    worker.validate_qlib_dataset(build.dataset_revision_id)

            self.assertEqual(raised.exception.code, "unknown_dataset")

if __name__ == "__main__":
    unittest.main()
