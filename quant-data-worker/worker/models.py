"""Public contracts for the quant data worker."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DateRange(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> "DateRange":
        if self.start > self.end:
            raise ValueError("start must not be after end")
        return self


class WorkerError(BaseModel):
    code: str
    message: str
    stage: str | None = None
    retryable: bool = False


class MarketDataSource(BaseModel):
    vendor: Literal["openbb"] = "openbb"
    provider: str
    frequency: Literal["1d"] = "1d"
    adjustment: str


class AdjustmentStatus(BaseModel):
    requested_policy: str
    effective_policy: str
    verification_status: Literal["unsupported", "unknown"] = "unsupported"


class ProviderEvidence(BaseModel):
    vendor: Literal["openbb"] = "openbb"
    provider_name: str = "unknown"
    vendor_version: str = "unknown"
    provider_adapter_version: str = "unknown"
    revision_evidence: Literal["unsupported", "unknown"] = "unsupported"


class MarketSessionSemantics(BaseModel):
    exchange: str = "unknown"
    timezone: str = "unknown"
    session_date_policy: Literal["provider_observation_date", "unknown"] = "unknown"
    calendar_source: Literal["returned_rows_union", "unknown"] = "unknown"


class SymbolSemantics(BaseModel):
    mapping_status: Literal["provider_passthrough", "unknown"] = "unknown"
    exchange_suffix_behavior: Literal["provider_defined_unverified", "unknown"] = (
        "unknown"
    )
    delisting_support: Literal["unsupported", "unknown"] = "unknown"


class UniverseSemantics(BaseModel):
    construction: Literal["user_supplied_symbols", "unknown"] = "unknown"
    membership_dates: Literal["unavailable", "unknown"] = "unknown"


class MissingDataSemantics(BaseModel):
    fill_behavior: Literal["none", "unknown"] = "unknown"
    missing_trading_days: Literal["reported_per_symbol", "unknown"] = "unknown"
    no_trade_behavior: Literal["missing_row", "unknown"] = "unknown"


class CorporateActionSemantics(BaseModel):
    split_behavior: Literal["provider_adjustment", "unadjusted", "unknown"] = "unknown"
    dividend_behavior: Literal["provider_adjustment", "unadjusted", "unknown"] = (
        "unknown"
    )
    verification_status: Literal["unsupported", "unknown"] = "unknown"


class FundamentalAvailability(BaseModel):
    availability_dates: Literal["unsupported", "unknown"] = "unsupported"
    historical_training_eligible: Literal[False] = False


class LookAheadSemantics(BaseModel):
    feature_lookahead_trading_days: int = Field(default=0, ge=0)
    label_lookahead_trading_days: int = Field(default=2, ge=0)
    policy: Literal["alpha158_close_t_plus_2_over_t_plus_1"] = (
        "alpha158_close_t_plus_2_over_t_plus_1"
    )


class EligibilityDecision(BaseModel):
    policy_version: Literal["1"] = "1"
    reasons: list[str] = Field(default_factory=list, max_length=30)


class MarketDataLimitations(BaseModel):
    point_in_time_status: Literal["unsupported", "unknown"] = "unsupported"
    survivorship_status: Literal["user_supplied", "unknown"] = "user_supplied"
    adjustment_status: AdjustmentStatus
    calendar_status: Literal["inferred_union", "unknown"] = "inferred_union"
    missing_data_policy: Literal["no_fill_per_symbol"] = "no_fill_per_symbol"
    provider_revision_status: Literal["unsupported", "unknown"] = "unsupported"
    production_eligibility: Literal["research_only", "blocked"] = "research_only"
    provider_evidence: ProviderEvidence = Field(default_factory=ProviderEvidence)
    market_session: MarketSessionSemantics = Field(default_factory=MarketSessionSemantics)
    symbol_semantics: SymbolSemantics = Field(default_factory=SymbolSemantics)
    universe_semantics: UniverseSemantics = Field(default_factory=UniverseSemantics)
    missing_data: MissingDataSemantics = Field(default_factory=MissingDataSemantics)
    corporate_actions: CorporateActionSemantics = Field(
        default_factory=CorporateActionSemantics
    )
    fundamental_availability: FundamentalAvailability = Field(
        default_factory=FundamentalAvailability
    )
    lookahead: LookAheadSemantics = Field(default_factory=LookAheadSemantics)
    eligibility_decision: EligibilityDecision = Field(default_factory=EligibilityDecision)

    @model_validator(mode="after")
    def enforce_fail_closed_eligibility(self) -> "MarketDataLimitations":
        # This worker currently has no qualified production provider. Keeping the
        # accepted vocabulary closed prevents metadata from upgrading yfinance.
        if self.production_eligibility == "research_only":
            unsupported = (
                self.point_in_time_status != "unsupported"
                or self.provider_evidence.provider_name not in {"unknown", "yfinance"}
            )
            if unsupported:
                raise ValueError("unqualified provider metadata cannot be research_only")
        return self


class FetchOhlcvRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=300)
    start_date: date
    end_date: date
    provider: Literal["yfinance"]
    frequency: Literal["1d"] = "1d"
    adjustment: Literal["auto", "adjusted", "unadjusted", "provider_default"] = "auto"
    idempotency_key: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}$",
    )

    @model_validator(mode="after")
    def validate_request(self) -> "FetchOhlcvRequest":
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        symbols = [symbol.strip().upper() for symbol in self.symbols]
        if any(not symbol for symbol in symbols):
            raise ValueError("symbols must not contain blank values")
        if len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be unique")
        self.symbols = symbols
        return self


class FactorSnapshotRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=300)
    as_of_date: date
    provider: Literal["yfinance"]
    idempotency_key: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}$",
    )

    @model_validator(mode="after")
    def validate_request(self) -> "FactorSnapshotRequest":
        symbols = [symbol.strip().upper() for symbol in self.symbols]
        if any(not symbol for symbol in symbols):
            raise ValueError("symbols must not contain blank values")
        if len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be unique")
        self.symbols = symbols
        return self


class FetchOhlcvResponse(BaseModel):
    ok: Literal[True] = True
    schema_version: Literal["1"] = "1"
    staging_revision_id: str
    dataset_staging_id: str
    request_fingerprint: str
    content_hash: str
    retrieved_at: str
    source: MarketDataSource
    symbols_requested: int
    symbols_loaded: int
    rows: int
    schema_: list[str] = Field(alias="schema")
    date_range: DateRange
    warnings: list[str] = Field(default_factory=list)
    limitations: MarketDataLimitations


class MarketDataValidation(BaseModel):
    ok: Literal[True] = True
    valid: bool
    dataset_staging_id: str
    rows: int
    symbols: list[str]
    date_range: DateRange | None = None
    quality_checks: dict[str, int | bool]
    missing_by_symbol: dict[str, int]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class FactorRecord(BaseModel):
    symbol: str
    market_cap: float | None = None
    pe_ratio: float | None = None
    price_to_book: float | None = None
    price_to_sales: float | None = None
    debt_to_equity: float | None = None
    return_on_equity: float | None = None
    revenue_growth: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    profit_margin: float | None = None
    currency: str | None = None
    period_ending: date | None = None


class FactorSnapshotLimitations(BaseModel):
    point_in_time_status: Literal["unsupported"] = "unsupported"
    availability_dates: Literal["unsupported"] = "unsupported"
    snapshot_semantics: Literal["current_retrieval_snapshot"] = (
        "current_retrieval_snapshot"
    )
    historical_training_eligible: Literal[False] = False
    production_eligibility: Literal["research_only"] = "research_only"
    provider_evidence: ProviderEvidence = Field(default_factory=ProviderEvidence)
    reasons: list[str] = Field(default_factory=list, max_length=20)


class FactorSnapshotResponse(BaseModel):
    ok: Literal[True] = True
    schema_version: Literal["1"] = "1"
    factor_snapshot_revision_id: str
    factor_snapshot_id: str
    request_fingerprint: str
    content_hash: str
    retrieved_at: str
    point_in_time_status: Literal["unsupported"] = "unsupported"
    provider: Literal["yfinance"]
    as_of_date: date
    symbols_requested: int
    symbols_loaded: int
    factors: list[FactorRecord]
    warnings: list[str] = Field(default_factory=list)
    limitations: FactorSnapshotLimitations = Field(
        default_factory=FactorSnapshotLimitations
    )


class BuildQlibDatasetRequest(BaseModel):
    dataset_staging_id: str
    dataset_alias: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,80}$",
    )
    dataset_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,80}$",
        description="Deprecated input alias; never used as the canonical revision ID.",
    )
    universe_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
    include_fields: list[Literal["open", "high", "low", "close", "volume", "factor"]] = Field(
        default_factory=lambda: ["open", "high", "low", "close", "volume", "factor"]
    )

    @model_validator(mode="after")
    def validate_fields(self) -> "BuildQlibDatasetRequest":
        if self.dataset_alias and self.dataset_id and self.dataset_alias != self.dataset_id:
            raise ValueError("dataset_alias and deprecated dataset_id must match")
        self.dataset_alias = self.dataset_alias or self.dataset_id
        if not self.dataset_alias:
            raise ValueError("dataset_alias is required")
        fields = list(dict.fromkeys(self.include_fields))
        if "close" not in fields or "factor" not in fields:
            raise ValueError("include_fields must include close and factor")
        self.include_fields = fields
        return self


class QlibDatasetBuildResponse(BaseModel):
    ok: Literal[True] = True
    schema_version: Literal["1"] = "1"
    dataset_revision_id: str
    dataset_id: str
    dataset_alias: str
    provider_uri: str
    source_staging_revision_id: str
    source_staging_id: str
    manifest_hash: str
    manifest_verified: Literal[True] = True
    calendar: dict[str, date | int]
    universe: dict[str, str | int]
    qlib_layout: dict[str, bool]
    warnings: list[str] = Field(default_factory=list)
    limitations: MarketDataLimitations


class QlibDatasetValidation(BaseModel):
    ok: Literal[True] = True
    valid: bool
    schema_version: Literal["1"] = "1"
    dataset_revision_id: str
    dataset_id: str
    provider_uri: str
    frequency: Literal["day"] = "day"
    start_date: date | None = None
    end_date: date | None = None
    max_test_end: date | None = None
    trading_days: int | None = None
    universes: list[str] = Field(default_factory=list)
    manifest_hash: str | None = None
    manifest_verified: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    limitations: MarketDataLimitations
