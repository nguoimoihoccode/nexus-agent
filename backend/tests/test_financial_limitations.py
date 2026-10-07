"""Fail-closed financial correctness contract tests."""

import unittest

from pydantic import ValidationError

from source.contracts.worker import FinancialLimitationsContract


def _base_limitations() -> dict:
    return {
        "point_in_time_status": "unsupported",
        "survivorship_status": "user_supplied",
        "adjustment_status": {
            "requested_policy": "auto",
            "effective_policy": "splits_and_dividends",
            "verification_status": "unsupported",
        },
        "calendar_status": "inferred_union",
        "missing_data_policy": "no_fill_per_symbol",
        "provider_revision_status": "unsupported",
        "production_eligibility": "research_only",
    }


class FinancialLimitationsContractTests(unittest.TestCase):
    def test_research_only_legacy_shape_remains_compatible(self) -> None:
        limitations = FinancialLimitationsContract.model_validate(_base_limitations())

        self.assertEqual(limitations.production_eligibility, "research_only")
        self.assertEqual(limitations.market_session.calendar_source, "unknown")

    def test_unverified_dataset_cannot_claim_production_eligibility(self) -> None:
        payload = {**_base_limitations(), "production_eligibility": "eligible"}

        with self.assertRaises(ValidationError):
            FinancialLimitationsContract.model_validate(payload)

    def test_fully_verified_policy_can_be_eligible(self) -> None:
        payload = {
            **_base_limitations(),
            "point_in_time_status": "verified",
            "survivorship_status": "point_in_time_universe",
            "adjustment_status": {
                "requested_policy": "adjusted",
                "effective_policy": "split_and_dividend_adjusted",
                "verification_status": "verified",
            },
            "calendar_status": "official_exchange",
            "provider_revision_status": "verified",
            "production_eligibility": "eligible",
            "provider_evidence": {"revision_evidence": "verified"},
            "market_session": {
                "exchange": "XNYS",
                "timezone": "America/New_York",
                "session_date_policy": "exchange_session_date",
                "calendar_source": "official_exchange",
            },
            "symbol_semantics": {
                "mapping_status": "verified",
                "exchange_suffix_behavior": "verified",
                "delisting_support": "verified",
            },
            "universe_semantics": {
                "construction": "point_in_time_membership",
                "membership_dates": "verified",
            },
            "corporate_actions": {
                "split_behavior": "verified",
                "dividend_behavior": "verified",
                "verification_status": "verified",
            },
        }

        limitations = FinancialLimitationsContract.model_validate(payload)

        self.assertEqual(limitations.production_eligibility, "eligible")


if __name__ == "__main__":
    unittest.main()
