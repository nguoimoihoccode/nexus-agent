"""Safe external source normalization and redaction tests."""

import unittest

from source.infrastructure.source_repository import _redact, normalize_source_locator


class ResearchSourceSafetyTests(unittest.TestCase):
    def test_url_is_normalized_without_fragment_and_non_http_is_rejected(self):
        self.assertEqual(
            normalize_source_locator("HTTPS://Example.COM/path?q=1#private"),
            "https://example.com/path?q=1",
        )
        self.assertIsNone(normalize_source_locator("file:///etc/passwd"))
        self.assertIsNone(normalize_source_locator("https://example.com:bad/path"))
        self.assertEqual(
            normalize_source_locator(
                "https://user:pass@example.com/path?access_token=never&q=safe"  # pragma: allowlist secret
            ),
            "https://example.com/path?q=safe",
        )

    def test_credential_shaped_excerpt_content_is_redacted(self):
        value = _redact("Authorization Bearer token-value password=hunter2")
        self.assertNotIn("token-value", value)
        self.assertNotIn("hunter2", value)


if __name__ == "__main__":
    unittest.main()
