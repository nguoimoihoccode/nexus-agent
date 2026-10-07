"""Deep Agents harness profile for the Nexus runtime."""

from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

NEXUS_EXCLUDED_TOOLS = frozenset(
    {
        "execute",
        "write_file",
        "edit_file",
        "ls",
        "read_file",
        "glob",
        "grep",
    }
)
_REGISTERED_PROFILE_KEYS: set[str] = set()


class ForbiddenToolMiddleware(AgentMiddleware):
    """Block excluded tools even if a forged call reaches the tool node."""

    def __init__(self, excluded: frozenset[str]) -> None:
        self.excluded = excluded

    def _blocked(self, request) -> ToolMessage | None:
        tool_name = str(request.tool_call.get("name") or "")
        if tool_name not in self.excluded:
            return None
        return ToolMessage(
            content="This tool is disabled by the Nexus security policy.",
            tool_call_id=str(request.tool_call.get("id") or "disabled-tool"),
            name=tool_name,
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        return self._blocked(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        return self._blocked(request) or await handler(request)


def _security_middleware() -> list[AgentMiddleware]:
    return [ForbiddenToolMiddleware(NEXUS_EXCLUDED_TOOLS)]


def nexus_harness_profile() -> HarnessProfile:
    """Return the project harness profile layered on top of Deep Agents defaults."""
    return HarnessProfile(
        excluded_tools=NEXUS_EXCLUDED_TOOLS,
        extra_middleware=_security_middleware,
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    )


def register_nexus_harness_profile(model_spec: str) -> None:
    """Register Nexus runtime defaults for the configured model and provider."""
    for key in _profile_keys_for_model(model_spec):
        if key in _REGISTERED_PROFILE_KEYS:
            continue
        register_harness_profile(key, nexus_harness_profile())
        _REGISTERED_PROFILE_KEYS.add(key)


def _profile_keys_for_model(model_spec: str) -> tuple[str, ...]:
    provider, separator, model_name = model_spec.partition(":")
    if separator and provider and model_name:
        return (provider, model_spec)
    return (model_spec,)
