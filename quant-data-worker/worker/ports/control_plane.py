"""Durable metadata port owned by the quant-data application layer."""

from contextlib import contextmanager
from typing import Any, Iterator, Protocol

from worker.models import (
    BuildQlibDatasetRequest,
    FactorSnapshotRequest,
    FactorSnapshotResponse,
    FetchOhlcvRequest,
    FetchOhlcvResponse,
    QlibDatasetBuildResponse,
)


class QuantDataControlPlaneError(RuntimeError):
    """A stable durable-metadata boundary failure."""


class QuantDataControlPlane(Protocol):
    def readiness(self) -> bool: ...

    def ingestion_guard(
        self,
        operation: str,
        request: Any,
        response_type: type[Any],
    ) -> Any: ...

    def record_staging(
        self,
        request: FetchOhlcvRequest,
        response: FetchOhlcvResponse,
    ) -> None: ...

    def record_factor_snapshot(
        self,
        request: FactorSnapshotRequest,
        response: FactorSnapshotResponse,
    ) -> None: ...

    def record_dataset(
        self,
        request: BuildQlibDatasetRequest,
        response: QlibDatasetBuildResponse,
        manifest: dict[str, Any],
    ) -> None: ...

    def record_validation(
        self,
        entity_id: str,
        entity_type: str,
        validation: dict[str, Any],
    ) -> None: ...

    def close(self) -> None: ...


class NullQuantDataControlPlane:
    """Local/focused-test adapter when PostgreSQL metadata is not configured."""

    def readiness(self) -> bool:
        return True

    @contextmanager
    def ingestion_guard(
        self,
        operation: str,
        request: Any,
        response_type: type[Any],
    ) -> Iterator[None]:
        del operation, request, response_type
        yield None

    def record_staging(self, request, response) -> None:
        del request, response

    def record_factor_snapshot(self, request, response) -> None:
        del request, response

    def record_dataset(self, request, response, manifest) -> None:
        del request, response, manifest

    def record_validation(self, entity_id, entity_type, validation) -> None:
        del entity_id, entity_type, validation

    def close(self) -> None:
        return None
