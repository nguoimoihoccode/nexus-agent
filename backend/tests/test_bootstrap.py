"""Focused tests for the backend composition root."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from source.bootstrap import BackendRuntime
from source.infrastructure.checkpointer import checkpoint_serializer


class BackendRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_checkpoint_serializer_rejects_pickle_and_unknown_modules(self) -> None:
        with patch.dict("os.environ", {"NEXUS_ENV": "test"}, clear=True):
            serializer = checkpoint_serializer()

        self.assertIsInstance(serializer, JsonPlusSerializer)
        self.assertFalse(serializer.pickle_fallback)
        self.assertIsNone(serializer._allowed_json_modules)
        self.assertIsNone(serializer._allowed_msgpack_modules)

    def test_production_checkpoint_serializer_encrypts_and_requires_a_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"NEXUS_ENV": "production", "LANGGRAPH_AES_KEY": "k" * 32},
            clear=True,
        ):
            serializer = checkpoint_serializer()
        self.assertIsInstance(serializer, EncryptedSerializer)

        kind, ciphertext = serializer.dumps_typed({"evidence": "sensitive"})

        self.assertTrue(kind.endswith("+aes"))
        self.assertNotIn(b"sensitive", ciphertext)
        self.assertEqual(
            serializer.loads_typed((kind, ciphertext)),
            {"evidence": "sensitive"},
        )

        with patch.dict("os.environ", {"NEXUS_ENV": "production"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "LANGGRAPH_AES_KEY"):
                checkpoint_serializer()

    async def test_prepare_initializes_shared_resources_once(self) -> None:
        pool = SimpleNamespace(open=AsyncMock(), wait=AsyncMock())
        agents = {
            "supervisor": SimpleNamespace(checkpointer=None),
            "researcher": SimpleNamespace(checkpointer=None),
        }
        checkpointer = object()
        initialize = AsyncMock(return_value=checkpointer)
        schema_checker = AsyncMock(return_value=None)
        runtime = BackendRuntime(
            pool,
            agents,
            checkpointer_initializer=initialize,
            schema_checker=schema_checker,
        )

        await runtime.prepare()
        await runtime.prepare()

        pool.open.assert_awaited_once()
        pool.wait.assert_awaited_once()
        schema_checker.assert_awaited_once_with(pool)
        initialize.assert_awaited_once_with(pool)
        self.assertTrue(all(agent.checkpointer is checkpointer for agent in agents.values()))

if __name__ == "__main__":
    unittest.main()
