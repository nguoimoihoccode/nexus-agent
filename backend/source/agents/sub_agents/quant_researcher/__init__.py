"""Quant researcher subagent definition."""

from deepagents.middleware.subagents import SubAgent

from source.agents.config import load_subagent_definition, skills_middleware_for_agent
from source.agents.config.structured_output import handoff_middleware
from source.agents.approval_middleware import governed_approval_middleware
from source.agents.governed_effects import governed_effect_middleware
from source.contracts import QuantExperimentHandoff
from source.tools.custom import ToolExecutionPolicy
from source.tools.quant import QuantTools

QUANT_RESEARCHER = load_subagent_definition("quant-researcher")
QUANT_RESEARCH_APPROVAL_POLICY = {
    "run_governed_qlib_experiment": {
        "allowed_decisions": ["approve", "reject"]
    },
    "record_experiment_interpretation": {
        "allowed_decisions": ["approve", "reject"]
    },
}


def _middleware():
    skills = skills_middleware_for_agent(QUANT_RESEARCHER.name)
    return [
        handoff_middleware(QuantExperimentHandoff),
        governed_approval_middleware(QUANT_RESEARCH_APPROVAL_POLICY),
        governed_effect_middleware(),
        *([skills] if skills else []),
        *ToolExecutionPolicy.quant_researcher(),
    ]


def build_subagent() -> SubAgent:
    """Build the supervisor-registered quant researcher subagent."""
    return {
        "name": QUANT_RESEARCHER.name,
        "description": QUANT_RESEARCHER.description,
        "system_prompt": QUANT_RESEARCHER.system_prompt,
        "tools": QuantTools.tools,
        "middleware": _middleware(),
    }
