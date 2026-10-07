from __future__ import annotations

import unittest
from dataclasses import replace

from source.application.publications import GovernedPublicationService
from source.domain import PublicationConflict, PublicationEffect
from source.integrations.ai_trader_client import AiTraderError


class FakePublicationRepository:
    def __init__(self) -> None:
        self.effects: dict[tuple[str, str], PublicationEffect] = {}
        self.arguments: dict[tuple[str, str], tuple[str, dict]] = {}
        self.events = 0

    async def reserve_approved_effect(self, **values):
        key = (values["actor_key"], values["idempotency_key"])
        identity = (values["action_digest"], values["normalized_arguments"])
        if key in self.effects:
            if self.arguments[key] != identity:
                raise PublicationConflict("publication_idempotency_conflict")
            effect = self.effects[key]
            if effect.status == "failed":
                effect = replace(effect, status="reserved", safe_error=None)
                self.effects[key] = effect
            return effect, False
        effect = PublicationEffect(
            actor_key=values["actor_key"],
            publication_id="pub_v1_test",
            publication_type=values["publication_type"],
            approval_request_id="apr_v1_test",
            action_digest=values["action_digest"],
            idempotency_key=values["idempotency_key"],
            status="reserved",
        )
        self.effects[key] = effect
        self.arguments[key] = identity
        return effect, True

    async def mark_published(self, effect, *, target_reference, **_values):
        key = (effect.actor_key, effect.idempotency_key)
        current = self.effects[key]
        if current.status == "published":
            return current
        current = replace(
            current,
            status="published",
            target_reference=target_reference,
        )
        self.effects[key] = current
        self.events += 1
        return current

    async def mark_failed(self, effect, *, safe_error):
        key = (effect.actor_key, effect.idempotency_key)
        current = replace(effect, status="failed", safe_error=safe_error)
        self.effects[key] = current
        return current


class GovernedPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_loss_after_worker_acceptance_reuses_remote_effect(self):
        repository = FakePublicationRepository()
        service = GovernedPublicationService(repository)
        remote: dict[str, dict] = {}
        calls = 0

        async def publisher(request):
            nonlocal calls
            calls += 1
            key = request["idempotency_key"]
            existing = remote.get(key)
            if existing is not None:
                return {**existing, "reused": True}
            remote[key] = {"success": True, "signal_id": 73}
            raise AiTraderError(
                "ai_trader_timeout",
                "Worker accepted the request before the connection was lost.",
                retryable=True,
                stage="response",
            )

        values = {
            "actor_key": "v1-" + "a" * 64,
            "publication_type": "strategy",
            "tool_name": "publish_ai_trader_strategy",
            "normalized_arguments": {
                "market": "us-stock",
                "title": "Evidence",
                "content": "Research only.",
            },
            "thread_id": "thread-1",
            "run_id": "run-1",
            "publisher": publisher,
            "idempotency_key": "publication-connection-loss",
        }
        with self.assertRaises(AiTraderError):
            await service.publish(**values)
        retried = await service.publish(**values)

        self.assertEqual(retried["signal_id"], 73)
        self.assertEqual(calls, 2)
        self.assertEqual(len(remote), 1)
        self.assertEqual(repository.events, 1)

    async def test_retry_reuses_one_effect_without_second_worker_call(self) -> None:
        repository = FakePublicationRepository()
        service = GovernedPublicationService(repository)
        calls = 0

        async def publisher(_request):
            nonlocal calls
            calls += 1
            return {"success": True, "signal_id": 42}

        values = {
            "actor_key": "v1-" + "a" * 64,
            "publication_type": "strategy",
            "tool_name": "publish_ai_trader_strategy",
            "normalized_arguments": {
                "market": "us-stock",
                "title": "Evidence",
                "content": "Research only.",
            },
            "thread_id": "thread-1",
            "run_id": "run-1",
            "publisher": publisher,
            "idempotency_key": "publication-test",
        }
        first = await service.publish(**values)
        retried = await service.publish(**values)

        self.assertEqual(first["signal_id"], 42)
        self.assertEqual(retried["signal_id"], 42)
        self.assertTrue(retried["reused"])
        self.assertEqual(calls, 1)
        self.assertEqual(repository.events, 1)

    async def test_same_key_rejects_changed_action(self) -> None:
        repository = FakePublicationRepository()
        service = GovernedPublicationService(repository)

        async def publisher(_request):
            return {"success": True, "signal_id": 1}

        values = {
            "actor_key": "v1-" + "a" * 64,
            "publication_type": "discussion",
            "tool_name": "publish_ai_trader_discussion",
            "normalized_arguments": {
                "market": "crypto",
                "title": "Evidence",
                "content": "First.",
            },
            "thread_id": "thread-1",
            "run_id": "run-1",
            "publisher": publisher,
            "idempotency_key": "publication-conflict",
        }
        await service.publish(**values)
        values["normalized_arguments"] = {
            **values["normalized_arguments"],
            "content": "Changed.",
        }

        with self.assertRaises(PublicationConflict):
            await service.publish(**values)

    async def test_actor_scope_allows_same_key_for_two_tenants(self) -> None:
        repository = FakePublicationRepository()
        service = GovernedPublicationService(repository)

        async def publisher(request):
            return {"success": True, "signal_id": len(repository.effects)}

        base = {
            "publication_type": "strategy",
            "tool_name": "publish_ai_trader_strategy",
            "normalized_arguments": {
                "market": "us-stock",
                "title": "Evidence",
                "content": "Research only.",
            },
            "thread_id": "thread-1",
            "run_id": "run-1",
            "publisher": publisher,
            "idempotency_key": "shared-key",
        }
        await service.publish(actor_key="v1-" + "a" * 64, **base)
        await service.publish(actor_key="v1-" + "b" * 64, **base)

        self.assertEqual(len(repository.effects), 2)


if __name__ == "__main__":
    unittest.main()
