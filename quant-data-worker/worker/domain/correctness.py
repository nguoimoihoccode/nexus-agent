"""Deterministic, fail-closed market-data correctness metadata."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)[:80]
    except PackageNotFoundError:
        return "unknown"


def market_data_limitations(
    *,
    provider: str,
    requested_adjustment: str,
    effective_adjustment: str,
) -> dict[str, Any]:
    adjusted = effective_adjustment == "splits_and_dividends"
    return {
        "point_in_time_status": "unsupported",
        "survivorship_status": "user_supplied",
        "adjustment_status": {
            "requested_policy": requested_adjustment,
            "effective_policy": effective_adjustment,
            "verification_status": "unsupported",
        },
        "calendar_status": "inferred_union",
        "missing_data_policy": "no_fill_per_symbol",
        "provider_revision_status": "unsupported",
        "production_eligibility": "research_only",
        "provider_evidence": {
            "vendor": "openbb",
            "provider_name": provider,
            "vendor_version": _package_version("openbb"),
            "provider_adapter_version": _package_version("openbb-yfinance"),
            "revision_evidence": "unsupported",
        },
        "market_session": {
            "exchange": "unknown",
            "timezone": "unknown",
            "session_date_policy": "provider_observation_date",
            "calendar_source": "returned_rows_union",
        },
        "symbol_semantics": {
            "mapping_status": "provider_passthrough",
            "exchange_suffix_behavior": "provider_defined_unverified",
            "delisting_support": "unsupported",
        },
        "universe_semantics": {
            "construction": "user_supplied_symbols",
            "membership_dates": "unavailable",
        },
        "missing_data": {
            "fill_behavior": "none",
            "missing_trading_days": "reported_per_symbol",
            "no_trade_behavior": "missing_row",
        },
        "corporate_actions": {
            "split_behavior": "provider_adjustment" if adjusted else "unadjusted",
            "dividend_behavior": "provider_adjustment" if adjusted else "unadjusted",
            "verification_status": "unsupported",
        },
        "fundamental_availability": {
            "availability_dates": "unsupported",
            "historical_training_eligible": False,
        },
        "lookahead": {
            "feature_lookahead_trading_days": 0,
            "label_lookahead_trading_days": 2,
            "policy": "alpha158_close_t_plus_2_over_t_plus_1",
        },
        "eligibility_decision": {
            "policy_version": "1",
            "reasons": [
                "point_in_time_unsupported",
                "user_supplied_universe_membership_dates_unavailable",
                "adjustment_unverified",
                "calendar_inferred",
                "provider_revision_unsupported",
                "delisting_support_unsupported",
            ],
        },
    }


def factor_snapshot_limitations(*, provider: str) -> dict[str, Any]:
    return {
        "point_in_time_status": "unsupported",
        "availability_dates": "unsupported",
        "snapshot_semantics": "current_retrieval_snapshot",
        "historical_training_eligible": False,
        "production_eligibility": "research_only",
        "provider_evidence": {
            "vendor": "openbb",
            "provider_name": provider,
            "vendor_version": _package_version("openbb"),
            "provider_adapter_version": _package_version("openbb-yfinance"),
            "revision_evidence": "unsupported",
        },
        "reasons": [
            "provider_does_not_prove_field_availability_dates",
            "current_snapshot_must_not_be_used_as_historical_training_features",
        ],
    }
