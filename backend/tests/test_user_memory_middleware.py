import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import SystemMessage, ToolMessage

from source.agents.memory import (
    UserMemoryIdempotencyMiddleware,
    UserMemoryPromptMiddleware,
)
from source.domain import IdempotencyDecision


ACTOR = "v1-" + "a" * 64


class Request:
    def __init__(self, *, system_message=None, messages=None, tool_call=None):
        self.system_message = system_message
        self.messages = messages or []
        self.tool_call = tool_call

    def override(self, **values):
        return Request(
            system_message=values.get("system_message", self.system_message),
            messages=values.get("messages", self.messages),
            tool_call=values.get("tool_call", self.tool_call),
        )


class ConnectionContext:
    async def __aenter__(self):
        return SimpleNamespace(execute=AsyncMock())

    async def __aexit__(self, *_args):
        return None


class Pool:
    @staticmethod
    def connection():
        return ConnectionContext()


class FakeControlRepository:
    def __init__(self):
        self.result_reference = None

    async def reserve_idempotency(self, **values):
        return IdempotencyDecision(
            actor_key=values["actor_key"],
            operation=values["operation"],
            idempotency_key=values["idempotency_key"],
            arguments_digest=values["arguments_digest"],
            state="completed" if self.result_reference else "reserved",
            result_reference=self.result_reference,
        ), self.result_reference is None

    async def complete_idempotency_with_event(self, **values):
        self.result_reference = values["result_reference"]
        return SimpleNamespace()


class UserMemoryMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_injection_marks_memory_as_untrusted(self):
        repository = SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(content="Use concise answers.")
            )
        )
        captured = None

        async def handler(request):
            nonlocal captured
            captured = request.messages[0].text
            self.assertEqual(request.system_message.text, "System authority.")
            return "ok"

        with (
            patch("source.agents.memory.optional_domain_pool", return_value=Pool()),
            patch(
                "source.agents.memory.PostgresUserMemoryRepository",
                return_value=repository,
            ),
            patch("source.agents.memory.current_actor_key", return_value=ACTOR),
        ):
            result = await UserMemoryPromptMiddleware().awrap_model_call(
                Request(system_message=SystemMessage(content="System authority.")),
                handler,
            )

        self.assertEqual(result, "ok")
        self.assertIn("<user_memory>", captured)
        self.assertIn("user-controlled reference context", captured)
        self.assertIn("Use concise answers.", captured)

    async def test_completed_tool_call_returns_prior_safe_result(self):
        control = FakeControlRepository()
        approvals = SimpleNamespace(
            require_approved=AsyncMock(return_value=SimpleNamespace()),
            record_execution=AsyncMock(),
        )
        handler = AsyncMock(
            return_value=ToolMessage(
                content=json.dumps(
                    {
                        "ok": True,
                        "changed": True,
                        "memory_key": "preference.response_style",
                        "revision": 1,
                        "content_hash": "sha256:" + "1" * 64,
                    }
                ),
                name="save_user_memory",
                tool_call_id="memory-call-1",
            )
        )
        request = Request(
            tool_call={
                "id": "memory-call-1",
                "name": "save_user_memory",
                "args": {
                    "memory_key": "preference.response_style",
                    "content": "private preference",
                },
            }
        )
        middleware = UserMemoryIdempotencyMiddleware()
        patches = (
            patch("source.agents.memory.optional_domain_pool", return_value=Pool()),
            patch("source.agents.memory.current_actor_key", return_value=ACTOR),
            patch(
                "source.agents.memory.PostgresDomainControlRepository",
                return_value=control,
            ),
            patch(
                "source.agents.memory.PostgresApprovalRepository",
                return_value=approvals,
            ),
            patch(
                "source.agents.memory.ensure_config",
                return_value={"run_id": "run-memory", "configurable": {}},
            ),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            first = await middleware.awrap_tool_call(request, handler)
            repeated = await middleware.awrap_tool_call(request, handler)

        self.assertEqual(json.loads(first.content), json.loads(repeated.content))
        handler.assert_awaited_once()
        self.assertNotIn("private preference", str(control.result_reference))


if __name__ == "__main__":
    unittest.main()
