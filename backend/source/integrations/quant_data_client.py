"""Async client for the internal quant data worker."""

from typing import Any

import httpx
from pydantic import SecretStr

from source.core.config import settings
from source.contracts import (
    QUANT_DATA_DATASET_BUILD,
    QUANT_DATA_DATASET_VALIDATION,
    QUANT_DATA_FACTOR_SNAPSHOT,
    QUANT_DATA_MARKET_FETCH,
    QUANT_DATA_MARKET_VALIDATION,
)
from source.integrations.worker_client import WorkerClientError, WorkerHttpClient


QuantDataWorkerError = WorkerClientError


class QuantDataClient(WorkerHttpClient):
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 20,
        get_retries: int = 2,
        token: SecretStr | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url,
            timeout_seconds=timeout_seconds,
            get_retries=get_retries,
            token=token or settings.nexus_quant_data_worker_token,
            transport=transport,
        )

    async def fetch_openbb_ohlcv(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/openbb/ohlcv",
            json=request,
            contract=QUANT_DATA_MARKET_FETCH,
        )

    async def fetch_factor_snapshot(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/factors/snapshot",
            json=request,
            contract=QUANT_DATA_FACTOR_SNAPSHOT,
        )

    async def validate_market_data(self, dataset_staging_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/staging/{dataset_staging_id}/validate",
            contract=QUANT_DATA_MARKET_VALIDATION,
        )

    async def build_qlib_dataset(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/qlib-datasets",
            json=request,
            contract=QUANT_DATA_DATASET_BUILD,
        )

    async def validate_qlib_dataset(self, dataset_revision_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/qlib-datasets/{dataset_revision_id}/validate",
            contract=QUANT_DATA_DATASET_VALIDATION,
        )
