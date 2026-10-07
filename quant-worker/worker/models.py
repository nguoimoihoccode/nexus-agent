"""Public contracts for the Qlib experiment worker."""

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ExperimentModel = Literal["lightgbm", "linear", "xgboost", "catboost"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class DateRange(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> "DateRange":
        if self.start > self.end:
            raise ValueError("start must not be after end")
        return self


class StrategyConfig(BaseModel):
    type: Literal["topk_dropout"] = "topk_dropout"
    topk: int = Field(default=50, ge=1, le=300)
    n_drop: int = Field(default=5, ge=0, le=100)
    account: float = Field(default=100_000_000, ge=10_000, le=10_000_000_000)
    open_cost: float = Field(default=0.0005, ge=0, le=0.05)
    close_cost: float = Field(default=0.0015, ge=0, le=0.05)
    min_cost: float = Field(default=5, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_drop(self) -> "StrategyConfig":
        if self.n_drop > self.topk:
            raise ValueError("n_drop must not exceed topk")
        return self


class ExperimentRequest(BaseModel):
    dataset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,80}$")
    universe: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
    feature_set: Literal["Alpha158"] = "Alpha158"
    model: ExperimentModel = "lightgbm"
    train: DateRange
    valid: DateRange
    test: DateRange
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    idempotency_key: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}$",
    )
    arguments_digest: str | None = Field(
        default=None,
        pattern=r"^(?:act_v1_|sha256:)[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_segments(self) -> "ExperimentRequest":
        if not (self.train.end < self.valid.start and self.valid.end < self.test.start):
            raise ValueError("train, valid and test ranges must be ordered and non-overlapping")
        return self


class WorkerError(BaseModel):
    code: str
    message: str
    stage: str | None = None
    retryable: bool = False


class DatasetInfo(BaseModel):
    dataset_id: str
    dataset_revision_id: str | None = None
    universe: str
    frequency: str = "day"
    ready: bool
    start_date: date | None = None
    end_date: date | None = None
    max_test_end: date | None = None
    trading_days: int | None = None
    manifest_hash: str | None = None
    manifest_verified: bool = False
    limitations: dict[str, Any] = Field(default_factory=dict)
    production_eligibility: Literal["research_only", "eligible", "blocked"] = "blocked"


class DatasetValidation(BaseModel):
    dataset_id: str
    dataset_revision_id: str | None = None
    universe: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    max_test_end: date | None = None
    trading_days: int | None = None
    manifest_hash: str | None = None
    manifest_verified: bool = False
    limitations: dict[str, Any] = Field(default_factory=dict)
    production_eligibility: Literal["research_only", "eligible", "blocked"] = "blocked"


class ExperimentAccepted(BaseModel):
    experiment_id: str
    status: JobStatus
    dataset_id: str


class ExperimentRecord(BaseModel):
    experiment_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    request: ExperimentRequest
    result: dict[str, Any] | None = None
    error: WorkerError | None = None
    progress_stage: str | None = None
    cancellation: dict[str, Any] | None = None


class CancellationRequest(BaseModel):
    reason_category: Literal["user_requested", "quota", "maintenance"] = (
        "user_requested"
    )


class ArtifactManifest(BaseModel):
    artifact_id: str = Field(pattern=r"^art_v1_[0-9a-f]{24}$")
    schema_version: Literal["1"] = "1"
    producer_type: Literal["qlib_experiment"] = "qlib_experiment"
    producer_id: str
    artifact_type: Literal["predictions", "report", "positions"]
    media_type: Literal[
        "application/vnd.apache.parquet",
        "application/x-python-pickle",
    ]
    size_bytes: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    retention_class: Literal["experiment_evidence"] = "experiment_evidence"
    storage_status: Literal[
        "ready", "expired", "deleted", "corrupted", "quarantined"
    ] = "ready"
    download_eligible: bool
    created_at: datetime
    expires_at: datetime | None = None
    reason: str | None = None
