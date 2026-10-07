"""Supervisor subagent registry."""

from source.agents.config import subagent_enabled
from source.agents.sub_agents.ai_trader_agent import (
    build_subagent as build_ai_trader_agent,
)
from source.agents.sub_agents.quant_data_agent import (
    build_subagent as build_quant_data_agent,
)
from source.agents.sub_agents.quant_researcher import (
    build_subagent as build_quant_researcher,
)
from source.agents.sub_agents.researcher import build_subagent as build_researcher

researcher = build_researcher()
quant_data_agent = build_quant_data_agent()
quant_researcher = build_quant_researcher()
ai_trader_agent = build_ai_trader_agent()

all_subagents = [
    agent
    for agent in [researcher, quant_data_agent, quant_researcher, ai_trader_agent]
    if subagent_enabled(agent["name"])
]
