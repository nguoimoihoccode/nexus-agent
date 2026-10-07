"""Request models for the Nexus AI-Trader worker."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

CsvValues = str | list[str] | None
MARKET_NEWS_CATEGORIES = {"equities", "macro", "crypto", "commodities"}


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    market: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=20000)
    tags: CsvValues = None
    challenge_key: str | None = None
    mission_key: str | None = None
    team_key: str | None = None
    actor_key: str | None = Field(default=None, pattern=r"^v1-[0-9a-f]{64}$")
    idempotency_key: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}$",
    )
    action_digest: str | None = Field(
        default=None,
        pattern=r"^act_v1_[0-9a-f]{64}$",
    )

    @field_validator("market")
    @classmethod
    def normalize_market(cls, value: str) -> str:
        return value.strip().lower()


class StrategyRequest(PublishRequest):
    symbols: CsvValues = None


class DiscussionRequest(PublishRequest):
    symbol: str | None = Field(default=None, max_length=32)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned or None


class MarketRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    categories: list[str] | None = None
    lookback_hours: int | None = Field(default=None, ge=1, le=720)
    limit: int | None = Field(default=None, ge=1, le=100)

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            category = str(value).strip().lower()
            if not category or category in seen:
                continue
            if category not in MARKET_NEWS_CATEGORIES:
                raise ValueError(f"Unsupported market news category: {category}")
            seen.add(category)
            normalized.append(category)
        return normalized or None
