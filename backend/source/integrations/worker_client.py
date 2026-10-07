"""Shared HTTP client primitives for internal worker services."""

import asyncio
import logging
from time import perf_counter
from typing import Any

import httpx
from pydantic import SecretStr, TypeAdapter

from source.contracts import ContractValidationError, validate_contract
from source.core.observability import correlation_headers, log_event
from source.security import current_actor_key

logger = logging.getLogger(__name__)


class WorkerClientError(RuntimeError):
    """A stable, user-safe worker failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.stage is not None:
            error["stage"] = self.stage
        if self.retryable is not None:
            error["retryable"] = self.retryable
        return {"ok": False, "error": error}


class WorkerHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 20,
        get_retries: int = 2,
        token: SecretStr | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.get_retries = get_retries
        self.token = token
        self.transport = transport

    def _request_headers(self, actor_key: str | None = None) -> dict[str, str]:
        headers = correlation_headers()
        if self.token:
            headers["Authorization"] = f"Bearer {self.token.get_secret_value()}"
        headers["X-Nexus-Actor-Key"] = actor_key or current_actor_key()
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        contract: TypeAdapter[Any] | None = None,
        actor_key: str | None = None,
    ) -> Any:
        attempts = self.get_retries + 1 if method == "GET" else 1
        last_error: WorkerClientError | None = None
        for attempt in range(attempts):
            headers = self._request_headers(actor_key)
            started_at = perf_counter()
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                ) as client:
                    response = await client.request(
                        method,
                        path,
                        json=json,
                        headers=headers,
                    )
                    response.raise_for_status()
                    payload = response.json()
                if not isinstance(payload, (dict, list)):
                    raise WorkerClientError(
                        "invalid_worker_response",
                        "Worker returned an invalid response.",
                    )
                if contract is not None:
                    try:
                        payload = validate_contract(payload, contract)
                    except ContractValidationError as exc:
                        raise WorkerClientError(
                            "invalid_worker_response",
                            "Worker returned a response that violated its contract.",
                            stage="contract",
                            retryable=False,
                        ) from exc
                log_event(
                    logger,
                    "worker.request.completed",
                    worker_url=self.base_url,
                    method=method,
                    path=path,
                    status_code=response.status_code,
                    attempt=attempt + 1,
                    duration_ms=round((perf_counter() - started_at) * 1000, 2),
                    request_id=headers["X-Request-ID"],
                )
                return payload
            except WorkerClientError:
                raise
            except httpx.TimeoutException as exc:
                error = WorkerClientError(
                    "quant_worker_timeout",
                    "Quant worker did not respond before the timeout.",
                )
            except httpx.HTTPStatusError as exc:
                log_event(
                    logger,
                    "worker.request.rejected",
                    level=logging.WARNING,
                    worker_url=self.base_url,
                    method=method,
                    path=path,
                    status_code=exc.response.status_code,
                    duration_ms=round((perf_counter() - started_at) * 1000, 2),
                    request_id=headers["X-Request-ID"],
                )
                raise _response_error(exc.response) from exc
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                error = WorkerClientError(
                    "quant_worker_unavailable",
                    "Quant worker is unavailable or returned invalid JSON.",
                )
            last_error = error
            log_event(
                logger,
                "worker.request.failed",
                level=logging.WARNING,
                worker_url=self.base_url,
                method=method,
                path=path,
                error_code=error.code,
                attempt=attempt + 1,
                will_retry=attempt + 1 < attempts,
                duration_ms=round((perf_counter() - started_at) * 1000, 2),
                request_id=headers["X-Request-ID"],
            )
            if attempt + 1 < attempts:
                await asyncio.sleep(0.1 * (attempt + 1))
        if last_error is None:
            raise WorkerClientError(
                "quant_worker_unavailable",
                "Quant worker is unavailable or returned invalid JSON.",
            )
        raise last_error

    async def _request_bytes(
        self,
        path: str,
        *,
        actor_key: str,
        max_bytes: int,
    ) -> tuple[bytes, str, str | None]:
        headers = self._request_headers(actor_key)
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                async with client.stream("GET", path, headers=headers) as response:
                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared and int(declared) > max_bytes:
                        raise WorkerClientError(
                            "artifact_too_large",
                            "Artifact exceeds the authenticated download limit.",
                        )
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise WorkerClientError(
                                "artifact_too_large",
                                "Artifact exceeds the authenticated download limit.",
                            )
                        chunks.append(chunk)
                    return (
                        b"".join(chunks),
                        response.headers.get("content-type", "application/octet-stream"),
                        response.headers.get("x-artifact-content-hash"),
                    )
        except WorkerClientError:
            raise
        except httpx.HTTPStatusError as exc:
            raise _response_error(exc.response) from exc
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise WorkerClientError(
                "quant_worker_unavailable",
                "Quant worker is unavailable while reading the artifact.",
            ) from exc


def _response_error(response: httpx.Response) -> WorkerClientError:
    try:
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            message = detail.get("message")
            if not isinstance(message, str):
                message = f"Worker rejected the request ({response.status_code})."
            stage = detail.get("stage") if isinstance(detail.get("stage"), str) else None
            retryable = (
                detail.get("retryable")
                if isinstance(detail.get("retryable"), bool)
                else None
            )
            return WorkerClientError(
                detail["code"],
                message[:300],
                stage=stage,
                retryable=retryable,
            )
        safe_detail = str(detail or "request rejected")[:300]
    except (ValueError, TypeError):
        safe_detail = "request rejected"
    return WorkerClientError(
        "quant_worker_rejected",
        f"Worker rejected the request ({response.status_code}): {safe_detail}",
    )
