"""Researcher subagent definition."""

from deepagents.middleware.subagents import SubAgent

from source.agents.config import load_subagent_definition, skills_middleware_for_agent
from source.agents.config.structured_output import handoff_middleware
from source.contracts import ResearchHandoff
from source.tools.custom import CustomTools, ToolExecutionPolicy

RESEARCHER = load_subagent_definition("researcher")


def _middleware():
    skills = skills_middleware_for_agent(RESEARCHER.name)
    return [
        handoff_middleware(ResearchHandoff),
        *([skills] if skills else []),
        *ToolExecutionPolicy.researcher(),
    ]


def build_subagent() -> SubAgent:
    """Build the supervisor-registered researcher subagent."""
    return {
        "name": RESEARCHER.name,
        "description": RESEARCHER.description,
        "system_prompt": RESEARCHER.system_prompt,
        "tools": CustomTools.researcher,
        "middleware": _middleware(),
    }
