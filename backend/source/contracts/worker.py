"""Stable response contracts for internal worker HTTP APIs."""

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)


class ContractValidationError(ValueError):
    """Raised when a successful worker response violates its public contract."""


class WorkerContract(BaseModel):
    """Validate stable fields while preserving forward-compatible additions."""

    model_config = ConfigDict(extra="allow", strict=True)


class AdjustmentStatusContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    requested_policy: str
    effective_policy: str
    verification_status: Literal["verified", "unsupported", "unknown"]


class ProviderEvidenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    vendor: str = "unknown"
    provider_name: str = "unknown"
    vendor_version: str = "unknown"
    provider_adapter_version: str = "unknown"
    revision_evidence: Literal["verified", "unsupported", "unknown"] = "unknown"


class MarketSessionSemanticsContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    exchange: str = "unknown"
    timezone: str = "unknown"
    session_date_policy: Literal[
        "exchange_session_date", "provider_observation_date", "unknown"
    ] = "unknown"
    calendar_source: Literal["official_exchange", "returned_rows_union", "unknown"] = (
        "unknown"
    )


class SymbolSemanticsContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mapping_status: Literal["verified", "provider_passthrough", "unknown"] = "unknown"
    exchange_suffix_behavior: Literal[
        "verified", "provider_defined_unverified", "unknown"
    ] = "unknown"
    delisting_support: Literal["verified", "unsupported", "unknown"] = "unknown"


class UniverseSemanticsContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    construction: Literal[
        "point_in_time_membership", "user_supplied_symbols", "unknown"
    ] = "unknown"
    membership_dates: Literal["verified", "unavailable", "unknown"] = "unknown"


class MissingDataSemanticsContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fill_behavior: Literal["none", "forward_fill", "drop", "unknown"] = "unknown"
    missing_trading_days: Literal["reported_per_symbol", "verified", "unknown"] = (
        "unknown"
    )
    no_trade_behavior: Literal["missing_row", "explicit_no_trade", "unknown"] = (
        "unknown"
    )


class CorporateActionSemanticsContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    split_behavior: Literal["provider_adjustment", "unadjusted", "verified", "unknown"] = (
        "unknown"
    )
    dividend_behavior: Literal[
        "provider_adjustment", "unadjusted", "verified", "unknown"
    ] = "unknown"
    verification_status: Literal["verified", "unsupported", "unknown"] = "unknown"


class FundamentalAvailabilityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    availability_dates: Literal["verified", "unsupported", "unknown"] = "unknown"
    historical_training_eligible: bool = False


class LookAheadSemanticsContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    feature_lookahead_trading_days: int = Field(default=0, ge=0, le=3650)
    label_lookahead_trading_days: int = Field(default=2, ge=0, le=3650)
    policy: str = "unknown"


class EligibilityDecisionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    policy_version: str = "1"
    reasons: list[str] = Field(default_factory=list, max_length=30)


class FinancialLimitationsContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    point_in_time_status: Literal["verified", "unsupported", "unknown"]
    survivorship_status: Literal[
        "point_in_time_universe", "current_membership", "user_supplied", "unknown"
    ]
    adjustment_status: AdjustmentStatusContract
    calendar_status: Literal["official_exchange", "inferred_union", "unknown"]
    missing_data_policy: str
    provider_revision_status: Literal["verified", "unsupported", "unknown"]
    production_eligibility: Literal["research_only", "eligible", "blocked"]
    provider_evidence: ProviderEvidenceContract = Field(
        default_factory=ProviderEvidenceContract
    )
    market_session: MarketSessionSemanticsContract = Field(
        default_factory=MarketSessionSemanticsContract
    )
    symbol_semantics: SymbolSemanticsContract = Field(
        default_factory=SymbolSemanticsContract
    )
    universe_semantics: UniverseSemanticsContract = Field(
        default_factory=UniverseSemanticsContract
    )
    missing_data: MissingDataSemanticsContract = Field(
        default_factory=MissingDataSemanticsContract
    )
    corporate_actions: CorporateActionSemanticsContract = Field(
        default_factory=CorporateActionSemanticsContract
    )
    fundamental_availability: FundamentalAvailabilityContract = Field(
        default_factory=FundamentalAvailabilityContract
    )
    lookahead: LookAheadSemanticsContract = Field(
        default_factory=LookAheadSemanticsContract
    )
    eligibility_decision: EligibilityDecisionContract = Field(
        default_factory=EligibilityDecisionContract
    )

    @model_validator(mode="after")
    def require_verified_production_evidence(self) -> "FinancialLimitationsContract":
        if self.production_eligibility != "eligible":
            return self
        verified = (
            self.point_in_time_status == "verified"
            and self.survivorship_status == "point_in_time_universe"
            and self.adjustment_status.verification_status == "verified"
            and self.calendar_status == "official_exchange"
            and self.provider_revision_status == "verified"
            and self.provider_evidence.revision_evidence == "verified"
            and self.market_session.calendar_source == "official_exchange"
            and self.market_session.exchange != "unknown"
            and self.market_session.timezone != "unknown"
            and self.symbol_semantics.mapping_status == "verified"
            and self.symbol_semantics.exchange_suffix_behavior == "verified"
            and self.symbol_semantics.delisting_support == "verified"
            and self.universe_semantics.construction == "point_in_time_membership"
            and self.universe_semantics.membership_dates == "verified"
            and self.corporate_actions.verification_status == "verified"
        )
        if not verified:
            raise ValueError(
                "production_eligibility=eligible requires verified market-data evidence"
            )
        return self


class DatasetInfoContract(WorkerContract):
    dataset_id: str
    dataset_revision_id: str | None = None
    universe: str
    ready: bool
    manifest_hash: str | None = None
    manifest_verified: bool = False
    limitations: FinancialLimitationsContract
    production_eligibility: Literal["research_only", "eligible", "blocked"]


class DatasetValidationContract(WorkerContract):
    dataset_id: str
    dataset_revision_id: str | None = None
    universe: str
    valid: bool
    manifest_hash: str | None = None
    manifest_verified: bool = False
    limitations: FinancialLimitationsContract
    production_eligibility: Literal["research_only", "eligible", "blocked"]


class ExperimentAcceptedContract(WorkerContract):
    experiment_id: str
    status: Literal[
        "queued",
        "leased",
        "running",
        "cancelling",
        "cancelled",
        "completed",
        "failed",
        "expired",
    ]
    dataset_id: str


class ExperimentResultContract(WorkerContract):
    experiment_id: str
    status: Literal[
        "queued",
        "leased",
        "running",
        "cancelling",
        "cancelled",
        "completed",
        "failed",
        "expired",
    ]


class ArtifactManifestContract(WorkerContract):
    artifact_id: str = Field(pattern=r"^art_v1_[0-9a-f]{24}$")
    schema_version: Literal["1"]
    producer_type: Literal["qlib_experiment"]
    producer_id: str
    artifact_type: Literal["predictions", "report", "positions"]
    media_type: Literal[
        "application/vnd.apache.parquet",
        "application/x-python-pickle",
    ]
    size_bytes: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    storage_status: Literal[
        "ready", "expired", "deleted", "corrupted", "quarantined"
    ]
    download_eligible: bool


class ArtifactTombstoneContract(WorkerContract):
    artifact_id: str = Field(pattern=r"^art_v1_[0-9a-f]{24}$")
    schema_version: Literal["1"]
    storage_status: Literal["expired", "deleted", "corrupted", "quarantined"]
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reason: str


class MarketFetchContract(WorkerContract):
    ok: Literal[True]
    schema_version: Literal["1"]
    staging_revision_id: str
    dataset_staging_id: str
    request_fingerprint: str
    content_hash: str
    retrieved_at: str
    rows: int = Field(ge=0)
    limitations: FinancialLimitationsContract


class FactorSnapshotContract(WorkerContract):
    ok: Literal[True]
    schema_version: Literal["1"]
    factor_snapshot_revision_id: str
    factor_snapshot_id: str
    request_fingerprint: str
    content_hash: str
    retrieved_at: str
    point_in_time_status: Literal["unsupported"]
    symbols_loaded: int = Field(ge=0)
    factors: list[dict[str, Any]]
    limitations: "FactorSnapshotLimitationsContract" = Field(
        default_factory=lambda: FactorSnapshotLimitationsContract()
    )


class FactorSnapshotLimitationsContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    point_in_time_status: Literal["unsupported"] = "unsupported"
    availability_dates: Literal["unsupported"] = "unsupported"
    snapshot_semantics: Literal["current_retrieval_snapshot"] = (
        "current_retrieval_snapshot"
    )
    historical_training_eligible: Literal[False] = False
    production_eligibility: Literal["research_only"] = "research_only"
    provider_evidence: ProviderEvidenceContract = Field(
        default_factory=ProviderEvidenceContract
    )
    reasons: list[str] = Field(default_factory=list, max_length=20)


class MarketValidationContract(WorkerContract):
    ok: Literal[True]
    dataset_staging_id: str
    valid: bool


class DatasetBuildContract(WorkerContract):
    ok: Literal[True]
    schema_version: Literal["1"]
    dataset_revision_id: str
    dataset_id: str
    dataset_alias: str
    source_staging_revision_id: str
    source_staging_id: str
    manifest_hash: str
    manifest_verified: Literal[True]
    limitations: FinancialLimitationsContract


class QlibDatasetValidationContract(WorkerContract):
    ok: Literal[True]
    schema_version: Literal["1"]
    dataset_revision_id: str
    dataset_id: str
    valid: bool
    manifest_hash: str | None
    manifest_verified: bool
    limitations: FinancialLimitationsContract


class MarketOverviewContract(WorkerContract):
    available: bool


class MarketNewsContract(WorkerContract):
    available: bool
    categories: list[dict[str, Any]]


class SignalFeedContract(WorkerContract):
    signals: list[dict[str, Any]]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool


class PublishResultContract(WorkerContract):
    success: Literal[True]
    signal_id: int = Field(ge=1)


class HeartbeatContract(WorkerContract):
    messages: list[dict[str, Any]]
    tasks: list[dict[str, Any]]


QLIB_DATASET_LIST = TypeAdapter(list[DatasetInfoContract])
QLIB_DATASET_VALIDATION = TypeAdapter(DatasetValidationContract)
QLIB_EXPERIMENT_ACCEPTED = TypeAdapter(ExperimentAcceptedContract)
QLIB_EXPERIMENT_RESULT = TypeAdapter(ExperimentResultContract)
QLIB_ARTIFACT_METADATA = TypeAdapter(
    ArtifactManifestContract | ArtifactTombstoneContract
)

QUANT_DATA_MARKET_FETCH = TypeAdapter(MarketFetchContract)
QUANT_DATA_FACTOR_SNAPSHOT = TypeAdapter(FactorSnapshotContract)
QUANT_DATA_MARKET_VALIDATION = TypeAdapter(MarketValidationContract)
QUANT_DATA_DATASET_BUILD = TypeAdapter(DatasetBuildContract)
QUANT_DATA_DATASET_VALIDATION = TypeAdapter(QlibDatasetValidationContract)

AI_TRADER_MARKET_OVERVIEW = TypeAdapter(MarketOverviewContract)
AI_TRADER_MARKET_NEWS = TypeAdapter(MarketNewsContract)
AI_TRADER_SIGNAL_FEED = TypeAdapter(SignalFeedContract)
AI_TRADER_PUBLISH_RESULT = TypeAdapter(PublishResultContract)
AI_TRADER_HEARTBEAT = TypeAdapter(HeartbeatContract)


def validate_contract(payload: Any, adapter: TypeAdapter[Any]) -> Any:
    """Validate and serialize a worker payload without leaking Pydantic models."""
    try:
        validated = adapter.validate_python(payload)
    except ValidationError as exc:
        raise ContractValidationError("Worker response violated its contract.") from exc
    return adapter.dump_python(validated, mode="json", by_alias=True)
