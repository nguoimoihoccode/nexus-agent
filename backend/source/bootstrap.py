"""Application composition root for LangGraph runtime resources."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from source.agents.config import register_nexus_harness_profile
from source.agents.factory import create_agent, create_standalone_agent
from source.agents.sub_agents import (
    ai_trader_agent,
    all_subagents,
    quant_data_agent,
    quant_researcher,
    researcher,
)
from source.core.config import create_model, settings
from source.core.observability import log_event
from source.infrastructure.checkpointer import initialize_checkpointer
from source.infrastructure.database import create_db_pool
from source.infrastructure.migrations import verify_domain_schema
from source.infrastructure.event_repository import PostgresProductEventRepository
from source.infrastructure.runtime_resources import set_domain_pool
from source.security import database_actor_context

logger = logging.getLogger(__name__)


class BackendRuntime:
    """Own shared graph instances and their database lifecycle."""

    def __init__(
        self,
        pool: Any,
        agents: dict[str, Any],
        *,
        checkpointer_initializer: Callable[[Any], Awaitable[Any]],
        schema_checker: Callable[[Any], Awaitable[None]],
    ) -> None:
        self.pool = pool
        self.agents = agents
        self._checkpointer_initializer = checkpointer_initializer
        self._schema_checker = schema_checker
        self._ready = False
        self._lock = asyncio.Lock()

    async def prepare(self) -> None:
        """Open shared resources and attach one checkpointer exactly once."""
        if self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            log_event(logger, "backend.resources.initializing")
            await self.pool.open()
            await self.pool.wait()
            await self._schema_checker(self.pool)
            set_domain_pool(self.pool)
            checkpointer = await self._checkpointer_initializer(self.pool)
            for agent in self.agents.values():
                agent.checkpointer = checkpointer
            self._ready = True
            log_event(logger, "backend.resources.ready")

    @asynccontextmanager
    async def graph_context(self, config: dict[str, Any], graph_name: str):
        """Yield a prepared graph and emit its durable run lifecycle."""
        await self.prepare()
        configurable = config.get("configurable") or {}
        configuration = config.get("configuration") or {}
        user = configurable.get("langgraph_auth_user") or configuration.get(
            "langgraph_auth_user"
        )
        actor_key = user.get("identity") if isinstance(user, dict) else None
        metadata = config.get("metadata") or {}
        run_id = str(config.get("run_id") or metadata.get("run_id") or "")
        event_repository = (
            PostgresProductEventRepository(self.pool)
            if hasattr(self.pool, "connection")
            else None
        )
        thread_id = str(configurable.get("thread_id") or metadata.get("thread_id") or "")
        if actor_key and run_id and event_repository is not None:
            with database_actor_context(str(actor_key)):
                await event_repository.append(
                    actor_key=str(actor_key),
                    thread_id=thread_id or None,
                    run_id=run_id,
                    event_type="run.started",
                    payload={"graph_name": graph_name},
                )
        try:
            yield self.agents[graph_name]
        except Exception:
            if actor_key and run_id and event_repository is not None:
                with database_actor_context(str(actor_key)):
                    await event_repository.append(
                        actor_key=str(actor_key),
                        thread_id=thread_id or None,
                        run_id=run_id,
                        event_type="run.failed",
                        payload={"graph_name": graph_name},
                    )
            raise
        else:
            if actor_key and run_id and event_repository is not None:
                with database_actor_context(str(actor_key)):
                    await event_repository.append(
                        actor_key=str(actor_key),
                        thread_id=thread_id or None,
                        run_id=run_id,
                        event_type="run.completed",
                        payload={"graph_name": graph_name},
                    )


def create_backend_runtime(
    *,
    model_factory: Callable[[], Any] = create_model,
    pool_factory: Callable[[], Any] = create_db_pool,
    checkpointer_initializer: Callable[[Any], Awaitable[Any]] = initialize_checkpointer,
    schema_checker: Callable[[Any], Awaitable[None]] = verify_domain_schema,
) -> BackendRuntime:
    """Compose graphs and infrastructure with injectable boundary factories."""
    register_nexus_harness_profile(settings.model)
    agents = {
        "supervisor": create_agent(model_factory(), subagents=all_subagents),
        "researcher": create_standalone_agent(model_factory(), researcher),
        "quant-data-agent": create_standalone_agent(model_factory(), quant_data_agent),
        "quant-researcher": create_standalone_agent(model_factory(), quant_researcher),
        "ai-trader-agent": create_standalone_agent(model_factory(), ai_trader_agent),
    }
    return BackendRuntime(
        pool_factory(),
        agents,
        checkpointer_initializer=checkpointer_initializer,
        schema_checker=schema_checker,
    )
