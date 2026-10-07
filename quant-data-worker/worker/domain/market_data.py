"""Stable market-data vocabulary independent of HTTP and provider SDKs."""

STAGING_SCHEMA = ["date", "symbol", "open", "high", "low", "close", "volume", "factor"]
OHLC_FIELDS = ["open", "high", "low", "close"]
FACTOR_FIELDS = [
    "symbol",
    "market_cap",
    "pe_ratio",
    "price_to_book",
    "price_to_sales",
    "debt_to_equity",
    "return_on_equity",
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "profit_margin",
    "currency",
    "period_ending",
]
DEFAULT_WARNINGS = ["Universe is user-provided and may contain survivorship bias."]
FACTOR_WARNINGS = [
    "Factor snapshot is current/provider-returned data, not point-in-time historical fundamentals.",
    "Do not use factor snapshot as model training data in the MVP.",
]
LABEL_LOOKAHEAD_DAYS = 2


class WorkerError(Exception):
    """Stable, user-safe quant data worker error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str = "ingestion",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = retryable

    def as_detail(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
            "retryable": self.retryable,
        }
