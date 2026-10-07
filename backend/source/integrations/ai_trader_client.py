"""Async client for the AI-Trader worker API."""

import logging
from time import perf_counter
from typing import Any

import httpx
from pydantic import SecretStr

from source.contracts import (
    AI_TRADER_HEARTBEAT,
    AI_TRADER_MARKET_NEWS,
    AI_TRADER_MARKET_OVERVIEW,
    AI_TRADER_PUBLISH_RESULT,
    AI_TRADER_SIGNAL_FEED,
    ContractValidationError,
    validate_contract,
)
from source.core.observability import correlation_headers, log_event

logger = logging.getLogger(__name__)


class AiTraderError(RuntimeError):
    """A stable, user-safe AI-Trader failure."""

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


class AiTraderClient:
    """HTTP wrapper around the AI-Trader worker API."""

    def __init__(
        self,
        base_url: str,
        *,
        token: SecretStr | str | None = None,
        timeout_seconds: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _token_value(self) -> str | None:
        if self.token is None:
            return None
        if isinstance(self.token, SecretStr):
            return self.token.get_secret_value()
        return str(self.token)

    def _headers(self, *, auth_required: bool) -> dict[str, str]:
        token = self._token_value()
        if auth_required and not token:
            raise AiTraderError(
                "ai_trader_auth_required",
                "AI-Trader token is required for this operation.",
                stage="auth",
            )
        if not token:
            return {}
        return {
            "Authorization": f"Bearer {token}",
            "X-Claw-Token": token,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth_required: bool = False,
        contract=None,
    ) -> Any:
        headers = self._headers(auth_required=auth_required)
        headers.update(correlation_headers())
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
                    params=_compact(params),
                    json=json,
                    headers=headers,
                )
                response.raise_for_status()
            payload = response.json()
            if contract is not None:
                payload = validate_contract(payload, contract)
        except AiTraderError:
            raise
        except ContractValidationError as exc:
            raise AiTraderError(
                "invalid_worker_response",
                "AI-Trader returned a response that violated its contract.",
                stage="contract",
                retryable=False,
            ) from exc
        except httpx.TimeoutException as exc:
            self._log_failure(method, path, headers, started_at, "ai_trader_timeout")
            raise AiTraderError(
                "ai_trader_timeout",
                "AI-Trader did not respond before the timeout.",
                stage="http",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            self._log_failure(
                method,
                path,
                headers,
                started_at,
                "ai_trader_rejected",
                status_code=exc.response.status_code,
            )
            raise _response_error(exc.response) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            self._log_failure(method, path, headers, started_at, "ai_trader_unavailable")
            raise AiTraderError(
                "ai_trader_unavailable",
                "AI-Trader is unavailable or returned invalid JSON.",
                stage="http",
                retryable=True,
            ) from exc
        if not isinstance(payload, (dict, list)):
            raise AiTraderError(
                "invalid_ai_trader_response",
                "AI-Trader returned an invalid response.",
                stage="http",
            )
        log_event(
            logger,
            "worker.request.completed",
            worker_url=self.base_url,
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
            request_id=headers["X-Request-ID"],
        )
        return payload

    def _log_failure(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        started_at: float,
        error_code: str,
        *,
        status_code: int | None = None,
    ) -> None:
        log_event(
            logger,
            "worker.request.failed",
            level=logging.WARNING,
            worker_url=self.base_url,
            method=method,
            path=path,
            status_code=status_code,
            error_code=error_code,
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
            request_id=headers["X-Request-ID"],
        )

    async def market_overview(self) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/market-intel/overview",
            contract=AI_TRADER_MARKET_OVERVIEW,
        )

    async def market_news(
        self,
        *,
        category: str | None = None,
        limit: int = 5,
        symbols: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/market-intel/news",
            params={"category": category, "limit": limit, "symbols": symbols},
            contract=AI_TRADER_MARKET_NEWS,
        )

    async def signal_feed(
        self,
        *,
        market: str | None = None,
        message_type: str | None = None,
        keyword: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/signals/feed",
            params={
                "market": market,
                "message_type": message_type,
                "keyword": keyword,
                "limit": limit,
            },
            contract=AI_TRADER_SIGNAL_FEED,
        )

    async def publish_strategy(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/signals/strategy",
            json=request,
            auth_required=True,
            contract=AI_TRADER_PUBLISH_RESULT,
        )

    async def publish_discussion(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/signals/discussion",
            json=request,
            auth_required=True,
            contract=AI_TRADER_PUBLISH_RESULT,
        )

    async def poll_heartbeat(self) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/claw/agents/heartbeat",
            auth_required=True,
            contract=AI_TRADER_HEARTBEAT,
        )


def _compact(values: dict[str, Any] | None) -> dict[str, Any] | None:
    if not values:
        return None
    return {
        key: value
        for key, value in values.items()
        if value is not None and value != "" and value != "all"
    }


def _response_error(response: httpx.Response) -> AiTraderError:
    code = "ai_trader_rejected"
    retryable = False
    if response.status_code in {401, 403}:
        code = "ai_trader_auth_failed"
    elif response.status_code == 429:
        code = "ai_trader_rate_limited"
        retryable = True
    elif response.status_code >= 500:
        code = "ai_trader_unavailable"
        retryable = True

    try:
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        safe_detail = str(detail or payload or "request rejected")[:300]
    except (ValueError, TypeError):
        safe_detail = "request rejected"
    return AiTraderError(
        code,
        f"AI-Trader rejected the request ({response.status_code}): {safe_detail}",
        stage="http",
        retryable=retryable,
    )
