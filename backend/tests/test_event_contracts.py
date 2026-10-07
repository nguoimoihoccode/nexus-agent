"""Tests for compact product-event type and payload governance."""

import unittest

from pydantic import ValidationError

from source.contracts import ProductEventEnvelope, validate_event_payload


class ProductEventContractTests(unittest.TestCase):
    def test_registered_payload_is_normalized_and_extra_fields_are_rejected(self):
        payload = validate_event_payload(
            "worker.accepted",
            {
                "worker": "quant-worker",
                "job_type": "qlib_experiment",
                "job_id": "exp_v1_test",
                "status": "queued",
            },
        )

        self.assertEqual(payload["recovery"], False)
        with self.assertRaises(ValidationError):
            validate_event_payload(
                "run.started",
                {"graph_name": "supervisor", "raw_prompt": "do not persist"},
            )

    def test_unknown_event_sensitive_nested_key_and_large_payload_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unregistered"):
            validate_event_payload("experiment.magic", {})
        with self.assertRaisesRegex(ValueError, "sensitive"):
            validate_event_payload(
                "publication.published",
                {
                    "publication_id": "pub",
                    "publication_type": "strategy",
                    "approval_request_id": "apr",
                    "action_digest": "act",
                    "target_reference": {"access_token": "redacted"},
                },
            )
        with self.assertRaisesRegex(ValueError, "compact event limit"):
            validate_event_payload(
                "publication.published",
                {
                    "publication_id": "pub",
                    "publication_type": "strategy",
                    "approval_request_id": "apr",
                    "action_digest": "act",
                    "target_reference": {"value": "x" * 40_000},
                },
            )

    def test_envelope_rejects_payload_for_the_wrong_event_type(self):
        with self.assertRaises(ValidationError):
            ProductEventEnvelope.model_validate(
                {
                    "event_id": "evt_v1_" + "1" * 32,
                    "schema_version": 1,
                    "sequence": 1,
                    "actor_key": "v1-" + "2" * 64,
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "event_type": "run.started",
                    "occurred_at": "2026-07-14T00:00:00Z",
                    "sensitivity": "internal",
                    "payload": {"job_id": "wrong"},
                }
            )

    def test_approval_expiry_has_a_registered_bounded_reason(self):
        payload = validate_event_payload(
            "approval.expired",
            {
                "approval_request_id": "apr_v1_test",
                "action_digest": "act_v1_test",
                "tool_name": "save_user_memory",
                "target_boundary": "postgres:user-memory",
                "status": "expired",
                "reason": "approval_ttl_exceeded",
            },
        )

        self.assertEqual(payload["status"], "expired")

    def test_approval_decision_records_human_or_lease_source(self):
        payload = validate_event_payload(
            "approval.decided",
            {
                "approval_request_id": "apr_v1_test",
                "action_digest": "act_v1_test",
                "tool_name": "prepare_qlib_dataset",
                "target_boundary": "quant-data-worker:dataset",
                "decision": "approve",
                "status": "approved",
                "decision_source": "authorization_lease",
            },
        )

        self.assertEqual(payload["decision_source"], "authorization_lease")

    def test_memory_save_event_requires_a_stable_fact_key(self):
        payload = validate_event_payload(
            "memory.updated",
            {
                "tool_call_id": "memory-call-1",
                "operation": "save",
                "changed": True,
                "memory_key": "identity.name",
                "revision": 2,
                "content_hash": "sha256:" + "1" * 64,
            },
        )

        self.assertEqual(payload["memory_key"], "identity.name")
        with self.assertRaisesRegex(ValidationError, "require memory_key"):
            validate_event_payload(
                "memory.updated",
                {
                    "tool_call_id": "memory-call-2",
                    "operation": "save",
                    "changed": True,
                },
            )


if __name__ == "__main__":
    unittest.main()
