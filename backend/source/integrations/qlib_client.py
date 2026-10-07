"""Async client for the internal Qlib worker."""

from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from source.core.config import settings
from source.contracts import (
    QLIB_ARTIFACT_METADATA,
    QLIB_DATASET_LIST,
    QLIB_DATASET_VALIDATION,
    QLIB_EXPERIMENT_ACCEPTED,
    QLIB_EXPERIMENT_RESULT,
)
from source.integrations.worker_client import WorkerClientError, WorkerHttpClient


QuantWorkerError = WorkerClientError


class QlibClient(WorkerHttpClient):
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
            token=token or settings.nexus_quant_worker_token,
            transport=transport,
        )

    async def list_datasets(
        self,
        *,
        actor_key: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/v1/datasets",
            contract=QLIB_DATASET_LIST,
            actor_key=actor_key,
        )

    async def validate_dataset(
        self,
        dataset_id: str,
        universe: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/datasets/{quote(dataset_id)}/validate?universe={quote(universe)}",
            contract=QLIB_DATASET_VALIDATION,
        )

    async def submit_experiment(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/experiments",
            json=request,
            contract=QLIB_EXPERIMENT_ACCEPTED,
        )

    async def get_result(
        self,
        experiment_id: str,
        *,
        actor_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/experiments/{experiment_id}/result",
            contract=QLIB_EXPERIMENT_RESULT,
            actor_key=actor_key,
        )

    async def get_artifact(
        self,
        artifact_id: str,
        *,
        actor_key: str,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/artifacts/{quote(artifact_id)}",
            contract=QLIB_ARTIFACT_METADATA,
            actor_key=actor_key,
        )

    async def download_artifact(
        self,
        artifact_id: str,
        *,
        actor_key: str,
        max_bytes: int,
    ) -> tuple[bytes, str, str | None]:
        return await self._request_bytes(
            f"/v1/artifacts/{quote(artifact_id)}/content",
            actor_key=actor_key,
            max_bytes=max_bytes,
        )
