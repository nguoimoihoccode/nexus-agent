"""Trusted, agent-scoped skill injection without model-visible file tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import SystemMessage

from source.agents.config.locations import DEEPAGENTS_DIR, skill_names_for_agent

MAX_SKILL_BYTES = 32 * 1024
MAX_AGENT_SKILL_BYTES = 64 * 1024


def _append_system_message(
    system_message: SystemMessage | None,
    text: str,
) -> SystemMessage:
    blocks = list(system_message.content_blocks) if system_message else []
    if blocks:
        text = f"\n\n{text}"
    blocks.append({"type": "text", "text": text})
    return SystemMessage(content_blocks=blocks)


def _frontmatter(content: str, path: Path) -> dict[str, str]:
    if not content.startswith("---\n"):
        raise ValueError(f"{path} must start with YAML frontmatter.")
    try:
        _, raw, _body = content.split("---\n", 2)
    except ValueError as exc:
        raise ValueError(f"{path} has malformed YAML frontmatter.") from exc
    metadata: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"{path} frontmatter line is missing ':'.")
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata


def _load_assigned_skills(agent_name: str) -> tuple[str, ...]:
    skills_root = (DEEPAGENTS_DIR / "skills").resolve()
    contents: list[str] = []
    total_bytes = 0
    for skill_name in skill_names_for_agent(agent_name):
        path = (skills_root / skill_name / "SKILL.md").resolve()
        if not path.is_relative_to(skills_root) or path.parent.name != skill_name:
            raise ValueError(f"Configured skill '{skill_name}' escapes the skill root.")
        data = path.read_bytes()
        if len(data) > MAX_SKILL_BYTES:
            raise ValueError(
                f"Configured skill '{skill_name}' exceeds {MAX_SKILL_BYTES} bytes."
            )
        total_bytes += len(data)
        if total_bytes > MAX_AGENT_SKILL_BYTES:
            raise ValueError(
                f"Assigned skills for '{agent_name}' exceed "
                f"{MAX_AGENT_SKILL_BYTES} bytes."
            )
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Configured skill '{skill_name}' is not UTF-8.") from exc
        metadata = _frontmatter(content, path)
        if metadata.get("name") != skill_name or not metadata.get("description"):
            raise ValueError(
                f"Configured skill '{skill_name}' has invalid frontmatter."
            )
        contents.append(content.strip())
    return tuple(contents)


class AssignedSkillsMiddleware(AgentMiddleware):
    """Inject only registry-assigned, repository-trusted skill instructions."""

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self.skill_names = tuple(skill_names_for_agent(agent_name))
        self.skill_contents = _load_assigned_skills(agent_name)
        rendered = []
        for skill_name, content in zip(
            self.skill_names,
            self.skill_contents,
            strict=True,
        ):
            rendered.append(
                "<trusted_assigned_skill "
                f'name="{skill_name}">\n{content}\n</trusted_assigned_skill>'
            )
        self.system_prompt = (
            "The following repository-owned skills are trusted system instructions. "
            "They are already complete; do not try to read them from the filesystem.\n\n"
            + "\n\n".join(rendered)
        )

    def modify_request(self, request: ModelRequest) -> ModelRequest:
        return request.override(
            system_message=_append_system_message(
                request.system_message,
                self.system_prompt,
            )
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self.modify_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self.modify_request(request))


def skills_middleware_for_agent(agent_name: str) -> AssignedSkillsMiddleware | None:
    """Return trusted skill injection when the agent has assigned skills."""
    if not skill_names_for_agent(agent_name):
        return None
    return AssignedSkillsMiddleware(agent_name)


__all__ = [
    "MAX_AGENT_SKILL_BYTES",
    "MAX_SKILL_BYTES",
    "AssignedSkillsMiddleware",
    "skills_middleware_for_agent",
]
