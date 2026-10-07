"""Ports consumed by AI-Trader application services."""

from typing import Any, Protocol


class MarketNewsProvider(Protocol):
    def parameters(
        self,
        definition: dict[str, str | dict[str, str]],
        *,
        api_key: str,
        lookback_hours: int,
        limit: int,
    ) -> dict[str, Any]: ...
    def fetch(self, category: str, params: dict[str, Any]) -> dict[str, Any]: ...


class SignalRepository(Protocol):
    def health(self) -> dict[str, str | bool]: ...
    def publish_signal(self, **values: Any) -> dict[str, Any]: ...
    def signal_feed(self, **filters: Any) -> dict[str, Any]: ...
    def load_snapshot(self, snapshot_type: str, snapshot_key: str) -> Any: ...
    def upsert_snapshot(
        self,
        snapshot_type: str,
        snapshot_key: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None: ...
