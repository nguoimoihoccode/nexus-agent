"""Golden-vector tests for cross-service canonical identity compatibility."""

import json
import unittest
from pathlib import Path

from source.contracts.identity import (
    action_fingerprint,
    artifact_id,
    canonical_json_bytes,
    content_hash,
    request_fingerprint,
    revision_id,
    new_runtime_id,
)


class CanonicalIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "reference"
            / "canonical-identity-v1.json"
        )
        cls.fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    def test_request_vector(self) -> None:
        vector = self.fixture["request_vector"]

        self.assertEqual(
            canonical_json_bytes(vector["payload"]).decode("utf-8"),
            vector["canonical_json"],
        )
        self.assertEqual(
            request_fingerprint(vector["namespace"], vector["payload"]),
            vector["request_fingerprint"],
        )

    def test_action_vector(self) -> None:
        vector = self.fixture["action_vector"]

        self.assertEqual(
            action_fingerprint(vector["payload"]),
            vector["action_fingerprint"],
        )

    def test_content_and_revision_vector(self) -> None:
        vector = self.fixture["content_vector"]
        digest = content_hash(vector["namespace"], vector["payload"])

        self.assertEqual(digest, vector["content_hash"])
        self.assertEqual(
            revision_id("stg", digest),
            vector["staging_revision_id"],
        )

    def test_backend_owned_runtime_and_artifact_ids_are_versioned(self) -> None:
        digest = "sha256:" + "1" * 64

        self.assertEqual(artifact_id(digest), "art_v1_" + "1" * 24)
        self.assertRegex(new_runtime_id("event"), r"^evt_v1_[0-9a-f]{32}$")
        self.assertRegex(
            new_runtime_id("approval_request"),
            r"^apr_v1_[0-9a-f]{32}$",
        )
        with self.assertRaises(ValueError):
            new_runtime_id("model_supplied")


if __name__ == "__main__":
    unittest.main()
