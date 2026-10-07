"""Focused safety tests for lineage API property projection."""

import unittest

from source.infrastructure.lineage_repository import (
    _comparison_incompatibilities,
    _metric_evidence,
    _safe_properties,
)


class LineageProjectionTests(unittest.TestCase):
    def test_internal_locations_and_secret_shaped_fields_are_removed_recursively(self):
        projected = _safe_properties(
            {
                "artifact_id": "art_v1_test",
                "storage_key": "private/path",
                "nested": {
                    "provider_uri": "/host/data",
                    "access_token": "never",
                    "safe": [1, {"password": "never", "value": 2}],  # pragma: allowlist secret
                },
            }
        )

        self.assertEqual(
            projected,
            {
                "artifact_id": "art_v1_test",
                "nested": {"safe": [1, {"value": 2}]},
            },
        )

    def test_metric_evidence_is_fail_closed(self):
        metric = _metric_evidence(
            "lin_v1_test",
            {"name": "ic", "value": 0.1, "calculation_version": "v1"},
        )

        self.assertEqual(metric["evidence_status"], "incomplete")
        self.assertIn("artifact_ids", metric["missing_evidence"])

    def test_comparison_rejects_different_dataset_revisions(self):
        def dossier(dataset_id):
            return {
                "experiment": {
                    "dataset_revision_id": dataset_id,
                    "specification": {
                        "universe": "demo",
                        "feature_set": "Alpha158",
                        "train": {"start": "2020-01-01"},
                        "valid": {"start": "2020-02-01"},
                        "test": {"start": "2020-03-01"},
                    },
                },
                "limitations": {"lookahead": {"label_lookahead_trading_days": 2}},
                "metrics": [],
            }

        reasons = _comparison_incompatibilities([dossier("dsr_one"), dossier("dsr_two")])

        self.assertIn("dataset_revision_mismatch", {item["code"] for item in reasons})


if __name__ == "__main__":
    unittest.main()
