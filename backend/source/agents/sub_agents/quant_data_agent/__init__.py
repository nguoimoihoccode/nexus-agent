"""Quant data subagent definition."""

from deepagents.middleware.subagents import SubAgent

from source.agents.config import load_subagent_definition, skills_middleware_for_agent
from source.agents.config.structured_output import handoff_middleware
from source.agents.approval_middleware import governed_approval_middleware
from source.agents.governed_effects import governed_effect_middleware
from source.contracts import QuantDataHandoff
from source.tools.custom import ToolExecutionPolicy
from source.tools.quant_data import QuantDataTools

QUANT_DATA_AGENT = load_subagent_definition("quant-data-agent")
QUANT_DATA_APPROVAL_POLICY = {
    "prepare_qlib_dataset": {"allowed_decisions": ["approve", "reject"]},
    "fetch_factor_snapshot": {"allowed_decisions": ["approve", "reject"]},
}


def _middleware():
    skills = skills_middleware_for_agent(QUANT_DATA_AGENT.name)
    return [
        handoff_middleware(QuantDataHandoff),
        governed_approval_middleware(QUANT_DATA_APPROVAL_POLICY),
        governed_effect_middleware(),
        *([skills] if skills else []),
        *ToolExecutionPolicy.quant_data_agent(),
    ]


def build_subagent() -> SubAgent:
    """Build the supervisor-registered quant data subagent."""
    return {
        "name": QUANT_DATA_AGENT.name,
        "description": QUANT_DATA_AGENT.description,
        "system_prompt": QUANT_DATA_AGENT.system_prompt,
        "tools": QuantDataTools.tools,
        "middleware": _middleware(),
    }
