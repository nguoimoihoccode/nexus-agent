"""Ports consumed by the market-data application service."""

from pathlib import Path
from typing import Any, Protocol

from worker.domain import DatasetBuildIdentity, RevisionIdentity
from worker.models import BuildQlibDatasetRequest, FactorSnapshotRequest, FetchOhlcvRequest


class MarketDataFetcher(Protocol):
    def fetch_ohlcv(self, request: FetchOhlcvRequest) -> object: ...
    def fetch_factor_snapshot(self, symbol: str, provider: str) -> object: ...


class MarketDataRepository(Protocol):
    staging_dir: Path
    data_dir: Path

    def save_staging(
        self,
        request: FetchOhlcvRequest,
        frame: Any,
        warnings: list[str],
    ) -> RevisionIdentity: ...
    def save_factor_snapshot(
        self,
        request: FactorSnapshotRequest,
        frame: Any,
        warnings: list[str],
    ) -> RevisionIdentity: ...
    def load_staging(self, dataset_staging_id: str) -> Any: ...
    def read_staging_metadata(self, dataset_staging_id: str) -> dict[str, Any]: ...
    def read_factor_snapshot_metadata(self, factor_snapshot_id: str) -> dict[str, Any]: ...
    def build_dataset(
        self,
        request: BuildQlibDatasetRequest,
        frame: Any,
    ) -> DatasetBuildIdentity: ...
    def inspect_qlib_layout(self, dataset_id: str) -> tuple[list[str], list, list[str]]: ...
    def inspect_dataset_manifest(
        self,
        dataset_revision_id: str,
    ) -> tuple[list[str], str | None, bool]: ...
    def read_dataset_manifest(self, dataset_revision_id: str) -> dict[str, Any]: ...
    @staticmethod
    def effective_adjustment(adjustment: str) -> str: ...
