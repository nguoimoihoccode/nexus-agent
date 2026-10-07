"""AI-Trader worker subagent definition."""

from deepagents.middleware.subagents import SubAgent

from source.agents.config import load_subagent_definition, skills_middleware_for_agent
from source.agents.config.structured_output import handoff_middleware
from source.agents.approval_middleware import governed_approval_middleware
from source.contracts import AiTraderHandoff
from source.tools.ai_trader import AiTraderTools
from source.tools.custom import ToolExecutionPolicy

AI_TRADER_AGENT = load_subagent_definition("ai-trader-agent")
AI_TRADER_APPROVAL_POLICY = {
    "publish_ai_trader_strategy": {
        "allowed_decisions": ["approve", "reject"]
    },
    "publish_ai_trader_discussion": {
        "allowed_decisions": ["approve", "reject"]
    },
}


def _middleware():
    skills = skills_middleware_for_agent(AI_TRADER_AGENT.name)
    return [
        handoff_middleware(AiTraderHandoff),
        governed_approval_middleware(AI_TRADER_APPROVAL_POLICY),
        *([skills] if skills else []),
        *ToolExecutionPolicy.ai_trader_agent(),
    ]


def build_subagent() -> SubAgent:
    """Build the supervisor-registered AI-Trader worker subagent."""
    return {
        "name": AI_TRADER_AGENT.name,
        "description": AI_TRADER_AGENT.description,
        "system_prompt": AI_TRADER_AGENT.system_prompt,
        "tools": AiTraderTools.tools,
        "middleware": _middleware(),
    }
