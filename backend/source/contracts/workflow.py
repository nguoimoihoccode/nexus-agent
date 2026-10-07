"""Strict normalized intents accepted by deterministic quant workflows."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkflowIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1"] = "1"


class PrepareQlibDatasetIntent(WorkflowIntent):
    symbols: list[str] = Field(min_length=1, max_length=300)
    start_date: date
    end_date: date
    provider: Literal["yfinance"] = "yfinance"
    frequency: Literal["1d"] = "1d"
    adjustment: Literal[
        "auto", "adjusted", "unadjusted", "provider_default"
    ] = "auto"
    dataset_alias: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,80}$")
    universe_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
    include_fields: list[
        Literal["open", "high", "low", "close", "volume", "factor"]
    ] = Field(default_factory=lambda: ["open", "high", "low", "close", "volume", "factor"])

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, symbols: list[str]) -> list[str]:
        normalized = sorted(symbol.strip().upper() for symbol in symbols)
        if any(not symbol for symbol in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("symbols must be non-empty and unique")
        return normalized

    @model_validator(mode="after")
    def validate_dates_and_fields(self) -> "PrepareQlibDatasetIntent":
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        self.include_fields = list(dict.fromkeys(self.include_fields))
        if not {"close", "factor"}.issubset(self.include_fields):
            raise ValueError("include_fields must include close and factor")
        return self


class WorkflowDateRange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> "WorkflowDateRange":
        if self.start > self.end:
            raise ValueError("range start must not be after end")
        return self


class RunQlibExperimentIntent(WorkflowIntent):
    dataset_revision_id: str = Field(pattern=r"^dsr_v1_[0-9a-f]{24}$")
    universe: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
    model: Literal["lightgbm", "linear", "xgboost", "catboost"] = "lightgbm"
    train: WorkflowDateRange
    valid: WorkflowDateRange
    test: WorkflowDateRange
    topk: int = Field(default=50, ge=1, le=300)
    n_drop: int = Field(default=5, ge=0, le=100)
    account: float = Field(default=100_000_000, ge=10_000, le=10_000_000_000)
    open_cost: float = Field(default=0.0005, ge=0, le=0.05)
    close_cost: float = Field(default=0.0015, ge=0, le=0.05)
    min_cost: float = Field(default=5, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_segments(self) -> "RunQlibExperimentIntent":
        if not (self.train.end < self.valid.start and self.valid.end < self.test.start):
            raise ValueError("train, valid and test ranges must be ordered")
        if self.n_drop > self.topk:
            raise ValueError("n_drop must not exceed topk")
        return self
