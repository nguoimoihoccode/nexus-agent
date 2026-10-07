"""Agent runtime configuration and `.deepagents` wiring."""

from source.agents.config.locations import (
    BACKEND_ROOT,
    load_subagent_definition,
    subagent_enabled,
)
from source.agents.config.harness import (
    ForbiddenToolMiddleware,
    NEXUS_EXCLUDED_TOOLS,
    nexus_harness_profile,
    register_nexus_harness_profile,
)
from source.agents.config.skills import (
    AssignedSkillsMiddleware,
    skills_middleware_for_agent,
)

__all__ = [
    "BACKEND_ROOT",
    "AssignedSkillsMiddleware",
    "ForbiddenToolMiddleware",
    "NEXUS_EXCLUDED_TOOLS",
    "load_subagent_definition",
    "nexus_harness_profile",
    "register_nexus_harness_profile",
    "skills_middleware_for_agent",
    "subagent_enabled",
]
