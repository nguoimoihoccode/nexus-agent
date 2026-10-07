"""Focused tests for approval-backed quant execution capabilities."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import ToolMessage

from source.agents.governed_effects import (
    GovernedEffectDenied,
    GovernedEffectMiddleware,
    require_governed_effect,
)
from source.domain import ApprovalRecord, approval_action_identity, normalize_governed_arguments


ACTOR = "v1-" + "a" * 64


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self):
        self.db = SimpleNamespace(execute=AsyncMock())

    def connection(self):
        return _ConnectionContext(self.db)


class GovernedEffectTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_durable_control_fails_closed(self):
        middleware = GovernedEffectMiddleware()
        request = SimpleNamespace(
            tool_call={
                "name": "fetch_factor_snapshot",
                "id": "call-1",
                "args": {
                    "symbols": ["AAPL"],
                    "as_of_date": "2026-07-08",
                    "provider": "yfinance",
                },
            }
        )

        with patch(
            "source.agents.governed_effects.optional_domain_pool",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                GovernedEffectDenied,
                "control_unavailable",
            ):
                await middleware.awrap_tool_call(request, AsyncMock())

    async def test_approved_exact_call_receives_capability_and_records_execution(self):
        middleware = GovernedEffectMiddleware()
        pool = _Pool()
        args = {
            "symbols": ["MSFT", "AAPL"],
            "as_of_date": "2026-07-08",
            "provider": "yfinance",
        }
        request = SimpleNamespace(
            tool_call={
                "name": "fetch_factor_snapshot",
                "id": "call-approved",
                "args": args,
            }
        )
        normalized = normalize_governed_arguments(
            "fetch_factor_snapshot",
            args,
            tool_call_id="call-approved",
        )
        target, digest = approval_action_identity(
            actor_key=ACTOR,
            tool_name="fetch_factor_snapshot",
            normalized_arguments=normalized,
        )
        record = ApprovalRecord(
            actor_key=ACTOR,
            approval_request_id="apr_v1_" + "1" * 32,
            thread_id="thread-1",
            run_id="run-1",
            action_digest=digest,
            tool_name="fetch_factor_snapshot",
            target_boundary=target,
            normalized_arguments=normalized,
            status="approved",
        )
        repository = SimpleNamespace(
            require_approved=AsyncMock(return_value=record),
            record_execution=AsyncMock(),
        )

        async def handler(_request):
            require_governed_effect("fetch_factor_snapshot")
            return ToolMessage(
                content=(
                    '{"ok":true,"factor_snapshot_revision_id":'
                    '"fac_v1_0123456789abcdef01234567"}'
                ),
                tool_call_id="call-approved",
            )

        with (
            patch(
                "source.agents.governed_effects.optional_domain_pool",
                return_value=pool,
            ),
            patch(
                "source.agents.governed_effects.PostgresApprovalRepository",
                return_value=repository,
            ),
            patch(
                "source.agents.governed_effects.current_actor_key",
                return_value=ACTOR,
            ),
        ):
            result = await middleware.awrap_tool_call(request, handler)

        self.assertEqual(result.status, "success")
        repository.require_approved.assert_awaited_once_with(
            actor_key=ACTOR,
            action_digest=digest,
            tool_name="fetch_factor_snapshot",
            target_boundary=target,
            normalized_arguments=normalized,
        )
        execution = repository.record_execution.await_args.kwargs
        self.assertEqual(execution["status"], "executed")
        self.assertEqual(
            execution["reference"]["factor_snapshot_revision_id"],
            "fac_v1_0123456789abcdef01234567",
        )
        self.assertGreaterEqual(pool.db.execute.await_count, 2)

    def test_tool_call_identity_and_arguments_are_both_digest_bound(self):
        base = {
            "symbols": ["AAPL"],
            "as_of_date": "2026-07-08",
            "provider": "yfinance",
        }
        first = normalize_governed_arguments(
            "fetch_factor_snapshot",
            base,
            tool_call_id="call-1",
        )
        changed_args = normalize_governed_arguments(
            "fetch_factor_snapshot",
            {**base, "symbols": ["MSFT"]},
            tool_call_id="call-1",
        )
        changed_call = normalize_governed_arguments(
            "fetch_factor_snapshot",
            base,
            tool_call_id="call-2",
        )

        digests = {
            approval_action_identity(
                actor_key=ACTOR,
                tool_name="fetch_factor_snapshot",
                normalized_arguments=value,
            )[1]
            for value in (first, changed_args, changed_call)
        }

        self.assertEqual(len(digests), 3)


if __name__ == "__main__":
    unittest.main()
