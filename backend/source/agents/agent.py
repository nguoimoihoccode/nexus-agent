"""Public LangGraph entrypoints backed by the application composition root."""

from contextlib import asynccontextmanager

from langchain_core.runnables import RunnableConfig

from source.bootstrap import create_backend_runtime
from source.core.typing_compat import install_typing_extensions_aliases

install_typing_extensions_aliases()

_runtime = create_backend_runtime()

async def _prepare_server_resources() -> None:
    await _runtime.prepare()


@asynccontextmanager
async def _graph_context(config: RunnableConfig, graph_name: str):
    async with _runtime.graph_context(dict(config), graph_name) as agent:
        yield agent


@asynccontextmanager
async def graph(config: RunnableConfig):
    """Yield the supervisor graph with shared database integrations."""
    async with _graph_context(config, "supervisor") as agent:
        yield agent


@asynccontextmanager
async def researcher_graph(config: RunnableConfig):
    """Yield the standalone researcher graph for Studio traces."""
    async with _graph_context(config, "researcher") as agent:
        yield agent


@asynccontextmanager
async def quant_data_agent_graph(config: RunnableConfig):
    """Yield the standalone quant data agent graph for Studio traces."""
    async with _graph_context(config, "quant-data-agent") as agent:
        yield agent


@asynccontextmanager
async def quant_researcher_graph(config: RunnableConfig):
    """Yield the standalone quant researcher graph for Studio traces."""
    async with _graph_context(config, "quant-researcher") as agent:
        yield agent


@asynccontextmanager
async def ai_trader_agent_graph(config: RunnableConfig):
    """Yield the standalone AI-Trader graph for Studio traces."""
    async with _graph_context(config, "ai-trader-agent") as agent:
        yield agent
