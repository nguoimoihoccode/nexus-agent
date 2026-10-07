import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from source.agents.approval_middleware import GovernedApprovalMiddleware
from source.domain import AuthorizationLease, ApprovalRecord, approval_action_identity


ACTOR = "v1-" + "a" * 64
POLICY = {
    "publish_ai_trader_strategy": {
        "allowed_decisions": ["approve", "reject"]
    }
}


class FakeApprovalRepository:
    def __init__(self):
        self.requested = []
        self.decided = []

    async def request(self, **values):
        self.requested.append(values)
        return ApprovalRecord(
            actor_key=values["actor_key"],
            approval_request_id="apr_v1_" + "1" * 32,
            thread_id=values["thread_id"],
            run_id=values["run_id"],
            action_digest=values["action_digest"],
            tool_name=values["tool_name"],
            target_boundary=values["target_boundary"],
            normalized_arguments=values["normalized_arguments"],
            status="pending",
        ), True

    async def decide(self, record, **values):
        self.decided.append((record, values))
        return replace(
            record,
            status="approved" if values["decision"] == "approve" else "rejected",
        )


class FakeAuthorizationLeaseRepository:
    def __init__(self):
        self.lease = None
        self.requests = []

    async def get_active(self, **values):
        self.requests.append(values)
        return self.lease


class GovernedApprovalMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.middleware = GovernedApprovalMiddleware(interrupt_on=POLICY)
        self.tool_call = {
            "type": "tool_call",
            "id": "call-approval-1",
            "name": "publish_ai_trader_strategy",
            "args": {
                "market": "us-stock",
                "title": "Proposal",
                "content": "Research only.",
                "symbols": ["AAPL", "MSFT"],
                "tags": ["qlib"],
                "idempotency_key": "publish-key",
            },
        }
        self.state = {"messages": [AIMessage(content="", tool_calls=[self.tool_call])]}
        self.repository = FakeApprovalRepository()
        self.lease_repository = FakeAuthorizationLeaseRepository()

    async def _invoke(self, resume_result=None, *, resume_error=None):
        with (
            patch(
                "source.agents.approval_middleware.optional_domain_pool",
                return_value=SimpleNamespace(connection=True),
            ),
            patch(
                "source.agents.approval_middleware.PostgresApprovalRepository",
                return_value=self.repository,
            ),
            patch(
                "source.agents.approval_middleware.PostgresAuthorizationLeaseRepository",
                return_value=self.lease_repository,
            ),
            patch(
                "source.agents.approval_middleware.current_actor_key",
                return_value=ACTOR,
            ),
            patch(
                "source.agents.approval_middleware.ensure_config",
                return_value={
                    "run_id": "run-approval",
                    "configurable": {"thread_id": "thread-approval"},
                },
            ),
            patch.object(
                HumanInTheLoopMiddleware,
                "after_model",
                return_value=resume_result,
                side_effect=resume_error,
            ),
        ):
            return await self.middleware.aafter_model(self.state, SimpleNamespace())

    async def test_approved_resume_records_normalized_action_and_decision(self):
        result = {"messages": [AIMessage(content="", tool_calls=[self.tool_call])]}

        self.assertIs(await self._invoke(result), result)
        requested = self.repository.requested[0]
        self.assertEqual(requested["normalized_arguments"]["symbols"], "AAPL,MSFT")
        self.assertNotIn("idempotency_key", requested["normalized_arguments"])
        self.assertEqual(self.repository.decided[0][1]["decision"], "approve")
        self.assertEqual(
            self.repository.decided[0][1]["idempotency_key"],
            "publish-key",
        )

    async def test_rejected_resume_records_rejection_before_tool_execution(self):
        result = {
            "messages": [
                AIMessage(content="", tool_calls=[self.tool_call]),
                ToolMessage(
                    content="Rejected",
                    name="publish_ai_trader_strategy",
                    tool_call_id="call-approval-1",
                    status="error",
                ),
            ]
        }

        await self._invoke(result)

        self.assertEqual(self.repository.decided[0][1]["decision"], "reject")

    async def test_memory_rejection_records_only_hash_and_size(self):
        raw_memory = "private durable preference"
        self.middleware = GovernedApprovalMiddleware(
            interrupt_on={
                "save_user_memory": {"allowed_decisions": ["approve", "reject"]}
            }
        )
        self.tool_call = {
            "type": "tool_call",
            "id": "call-memory-1",
            "name": "save_user_memory",
            "args": {
                "memory_key": "preference.response_style",
                "content": raw_memory,
            },
        }
        self.state = {
            "messages": [AIMessage(content="", tool_calls=[self.tool_call])]
        }
        result = {
            "messages": [
                AIMessage(content="", tool_calls=[self.tool_call]),
                ToolMessage(
                    content="Rejected",
                    name="save_user_memory",
                    tool_call_id="call-memory-1",
                    status="error",
                ),
            ]
        }

        await self._invoke(result)

        normalized = self.repository.requested[0]["normalized_arguments"]
        self.assertEqual(normalized["memory_key"], "preference.response_style")
        self.assertEqual(normalized["content_bytes"], len(raw_memory.encode()))
        self.assertRegex(normalized["tool_call_hash"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn(raw_memory, str(normalized))
        self.assertEqual(self.repository.decided[0][1]["decision"], "reject")

    async def test_initial_interrupt_happens_after_request_is_recorded(self):
        with self.assertRaisesRegex(RuntimeError, "interrupt-now"):
            await self._invoke(resume_error=RuntimeError("interrupt-now"))

        self.assertEqual(len(self.repository.requested), 1)
        self.assertEqual(self.repository.decided, [])

    async def test_full_access_auto_approves_with_durable_lease_source(self):
        now = datetime.now(timezone.utc)
        self.lease_repository.lease = AuthorizationLease(
            actor_key=ACTOR,
            lease_id="azl_v1_" + "2" * 32,
            thread_id="thread-approval",
            mode="full_access",
            allow_sensitive=True,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

        should_interrupt = []

        def observe_base_policy(state, runtime):
            should_interrupt.append(
                self.middleware._should_interrupt(
                    self.tool_call,
                    self.middleware.interrupt_on[self.tool_call["name"]],
                    state,
                    runtime,
                )
            )
            return None

        result = await self._invoke(resume_error=observe_base_policy)

        self.assertIsNone(result)
        self.assertEqual(should_interrupt, [False])
        self.assertEqual(self.repository.decided[0][1]["decision"], "approve")
        self.assertEqual(
            self.repository.decided[0][1]["source"],
            "authorization-lease:" + self.lease_repository.lease.lease_id,
        )

    async def test_full_access_keeps_sensitive_effect_manual_by_default(self):
        now = datetime.now(timezone.utc)
        self.lease_repository.lease = AuthorizationLease(
            actor_key=ACTOR,
            lease_id="azl_v1_" + "3" * 32,
            thread_id="thread-approval",
            mode="full_access",
            allow_sensitive=False,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

        with self.assertRaisesRegex(RuntimeError, "interrupt-now"):
            await self._invoke(resume_error=RuntimeError("interrupt-now"))

        self.assertEqual(self.repository.decided, [])

    async def test_preblocked_tool_call_does_not_create_an_approval_request(self):
        self.state["messages"].append(
            ToolMessage(
                content="Tool call limit exceeded.",
                name="publish_ai_trader_strategy",
                tool_call_id="call-approval-1",
                status="error",
            )
        )

        self.assertIsNone(await self._invoke())
        self.assertEqual(self.repository.requested, [])

    def test_sync_execution_fails_closed_with_domain_database(self):
        with patch(
            "source.agents.approval_middleware.optional_domain_pool",
            return_value=SimpleNamespace(connection=True),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "require asynchronous graph execution",
            ):
                self.middleware.after_model(self.state, SimpleNamespace())

    def test_publication_identity_matches_raw_and_worker_normalized_arguments(self):
        raw_target, raw_digest = approval_action_identity(
            actor_key=ACTOR,
            tool_name="publish_ai_trader_strategy",
            normalized_arguments=self.tool_call["args"],
        )
        normalized_target, normalized_digest = approval_action_identity(
            actor_key=ACTOR,
            tool_name="publish_ai_trader_strategy",
            normalized_arguments={
                "market": "us-stock",
                "title": "Proposal",
                "content": "Research only.",
                "symbols": "AAPL,MSFT",
                "tags": "qlib",
            },
        )

        self.assertEqual(raw_target, normalized_target)
        self.assertEqual(raw_digest, normalized_digest)


if __name__ == "__main__":
    unittest.main()
