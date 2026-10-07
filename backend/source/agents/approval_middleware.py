"""Native HITL middleware that durably records proposals and decisions."""

from copy import deepcopy
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import ensure_config

from source.core.config import settings
from source.domain import (
    ApprovalConflict,
    approval_action_identity,
    normalize_governed_arguments,
)
from source.infrastructure.approval_repository import PostgresApprovalRepository
from source.infrastructure.authorization_repository import (
    PostgresAuthorizationLeaseRepository,
)
from source.infrastructure.runtime_resources import optional_domain_pool
from source.security import current_actor_key

_auto_approved_tool_call_ids: ContextVar[frozenset[str]] = ContextVar(
    "auto_approved_tool_call_ids",
    default=frozenset(),
)


class GovernedApprovalMiddleware(HumanInTheLoopMiddleware):
    """Use LangGraph interrupt/resume while making its audit state authoritative."""

    def after_model(self, state, runtime):
        """Fail closed if a governed production graph is invoked synchronously."""
        pool = optional_domain_pool()
        if pool is not None and hasattr(pool, "connection"):
            raise RuntimeError(
                "Governed approvals require asynchronous graph execution."
            )
        return super().after_model(state, runtime)

    def _should_interrupt(self, tool_call, config, state, runtime):
        if str(tool_call.get("id") or "") in _auto_approved_tool_call_ids.get():
            return False
        return super()._should_interrupt(tool_call, config, state, runtime)

    async def aafter_model(self, state, runtime):
        messages = state.get("messages") or []
        last_ai = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage)),
            None,
        )
        if last_ai is None:
            return None
        preblocked_ids = {
            message.tool_call_id
            for message in messages
            if isinstance(message, ToolMessage) and message.status == "error"
        }
        proposals = [
            deepcopy(tool_call)
            for tool_call in last_ai.tool_calls
            if (config := self.interrupt_on.get(tool_call["name"])) is not None
            and tool_call["id"] not in preblocked_ids
            and self._should_interrupt(tool_call, config, state, runtime)
        ]
        if not proposals:
            return None
        pool = optional_domain_pool()
        if pool is None or not hasattr(pool, "connection"):
            return super().after_model(state, runtime)

        config = ensure_config()
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        thread_id = str(
            configurable.get("thread_id")
            or metadata.get("thread_id")
            or f"thread-{proposals[0]['id']}"
        )
        run_id = str(
            config.get("run_id")
            or metadata.get("run_id")
            or f"approval-{proposals[0]['id']}"
        )
        actor_key = current_actor_key()
        repository = PostgresApprovalRepository(
            pool,
            approval_ttl_seconds=settings.nexus_approval_ttl_seconds,
        )
        lease = await PostgresAuthorizationLeaseRepository(pool).get_active(
            actor_key=actor_key,
            thread_id=thread_id,
        )
        if lease is not None and (
            lease.actor_key != actor_key or lease.thread_id != thread_id
        ):
            raise ApprovalConflict("authorization_lease_context_conflict")
        records = {}
        proposals_by_id = {proposal["id"]: proposal for proposal in proposals}
        auto_approved_ids = {
            str(proposal["id"])
            for proposal in proposals
            if lease is not None and lease.allows(str(proposal["name"]))
        }
        for proposal in proposals:
            normalized = normalize_governed_arguments(
                proposal["name"],
                proposal["args"],
                tool_call_id=str(proposal["id"]),
            )
            target, digest = approval_action_identity(
                actor_key=actor_key,
                tool_name=proposal["name"],
                normalized_arguments=normalized,
            )
            record, _created = await repository.request(
                actor_key=actor_key,
                thread_id=thread_id,
                run_id=run_id,
                action_digest=digest,
                tool_name=proposal["name"],
                target_boundary=target,
                normalized_arguments=normalized,
                agent_name=str(metadata.get("langgraph_node") or "supervisor"),
                tool_call_id=str(proposal["id"]),
            )
            records[proposal["id"]] = record
            if str(proposal["id"]) in auto_approved_ids:
                await repository.decide(
                    record,
                    decision="approve",
                    idempotency_key=proposal["args"].get("idempotency_key"),
                    source=f"authorization-lease:{lease.lease_id}",
                )

        # On the first pass this raises GraphInterrupt after the pending rows commit.
        # On resume it returns the approved calls and artificial rejection messages.
        token = _auto_approved_tool_call_ids.set(frozenset(auto_approved_ids))
        try:
            result = super().after_model(state, runtime)
        finally:
            _auto_approved_tool_call_ids.reset(token)
        if len(auto_approved_ids) == len(proposals):
            return result
        if result is None:
            raise ApprovalConflict("approval_resume_result_missing")
        result_messages = result.get("messages") or []
        rejection_ids = {
            message.tool_call_id
            for message in result_messages
            if isinstance(message, ToolMessage) and message.status == "error"
        }
        revised_ai = next(
            (message for message in result_messages if isinstance(message, AIMessage)),
            None,
        )
        approved_ids = {
            tool_call["id"] for tool_call in (revised_ai.tool_calls if revised_ai else [])
        }
        for proposal in proposals:
            tool_call_id = proposal["id"]
            if str(tool_call_id) in auto_approved_ids:
                continue
            if tool_call_id in rejection_ids:
                decision = "reject"
            elif tool_call_id in approved_ids:
                decision = "approve"
            else:
                raise ApprovalConflict("approval_resume_decision_missing")
            await repository.decide(
                records[tool_call_id],
                decision=decision,
                idempotency_key=(
                    proposals_by_id[tool_call_id]["args"].get("idempotency_key")
                ),
            )
        return result


def governed_approval_middleware(
    interrupt_on: dict[str, dict[str, Any]],
) -> GovernedApprovalMiddleware:
    return GovernedApprovalMiddleware(interrupt_on=interrupt_on)
