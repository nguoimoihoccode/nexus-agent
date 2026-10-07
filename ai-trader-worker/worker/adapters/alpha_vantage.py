"""Alpha Vantage NEWS_SENTIMENT provider adapter."""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

ALPHA_VANTAGE_DEFAULT_BASE_URL = "https://www.alphavantage.co/query"
MarketNewsFetcher = Callable[[str, dict[str, Any]], dict[str, Any]]


def validate_alpha_vantage_base_url(base_url: str) -> str:
    """Reject credential-bearing or unexpected production provider endpoints."""
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("ALPHA_VANTAGE_BASE_URL is invalid.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("ALPHA_VANTAGE_BASE_URL is invalid.")
    if os.environ.get("NEXUS_ENV", "development") == "production":
        if parsed.scheme != "https" or port not in {None, 443}:
            raise ValueError("ALPHA_VANTAGE_BASE_URL must use HTTPS in production.")
        configured_hosts = os.environ.get(
            "ALPHA_VANTAGE_ALLOWED_HOSTS", "www.alphavantage.co"
        )
        allowed_hosts = {
            value.strip().lower()
            for value in configured_hosts.split(",")
            if value.strip()
        }
        if parsed.hostname.lower() not in allowed_hosts:
            raise ValueError("ALPHA_VANTAGE_BASE_URL host is not allowlisted.")
    return base_url


class AlphaVantageNewsProvider:
    def __init__(
        self,
        base_url: str = ALPHA_VANTAGE_DEFAULT_BASE_URL,
        *,
        fetcher: MarketNewsFetcher | None = None,
    ) -> None:
        self.base_url = validate_alpha_vantage_base_url(base_url)
        self.fetcher = fetcher

    def parameters(
        self,
        definition: dict[str, str | dict[str, str]],
        *,
        api_key: str,
        lookback_hours: int,
        limit: int,
    ) -> dict[str, Any]:
        time_from = (
            datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        ).strftime("%Y%m%dT%H%M")
        params: dict[str, Any] = {
            "function": "NEWS_SENTIMENT",
            "sort": "LATEST",
            "time_from": time_from,
            "limit": limit,
            "apikey": api_key,
        }
        source_params = definition.get("alpha_vantage")
        if isinstance(source_params, dict):
            params.update(source_params)
        return params

    def fetch(self, category: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.fetcher is not None:
            return self.fetcher(category, dict(params))
        try:
            with httpx.Client(timeout=20) as client:
                response = client.get(self.base_url, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise RuntimeError("Alpha Vantage request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Alpha Vantage rejected the request ({exc.response.status_code})"
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise RuntimeError("Alpha Vantage returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Alpha Vantage returned an invalid response")
        return payload
