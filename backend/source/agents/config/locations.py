"""Locations and validated definitions for the Deep Agents harness."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from source.agents.config.registry import load_validated_nexus_config

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEEPAGENTS_RELATIVE_DIR = Path(".deepagents")
DEEPAGENTS_DIR = BACKEND_ROOT / DEEPAGENTS_RELATIVE_DIR
NEXUS_CONFIG_FILE = DEEPAGENTS_DIR / "nexus.json"


@dataclass(frozen=True)
class SubAgentDefinition:
    """Project subagent definition loaded from `.deepagents/agents`."""

    name: str
    description: str
    system_prompt: str
    model: str | None = None


def skill_names_for_agent(name: str) -> list[str]:
    """Return validated project skill names configured for an agent."""
    config = load_nexus_config()
    try:
        agent_config = config["agents"][name]
    except KeyError as exc:
        raise ValueError(f"Unknown configured agent '{name}'.") from exc
    skill_names = agent_config.get("skills", [])
    if not isinstance(skill_names, list):
        msg = f".deepagents/nexus.json agents.{name}.skills must be a list."
        raise ValueError(msg)
    return [_validated_skill_name(str(skill_name)) for skill_name in skill_names]


def subagent_enabled(name: str) -> bool:
    """Return whether a subagent should be registered with the supervisor."""
    config = load_nexus_config()
    try:
        agent_config = config["agents"][name]
    except KeyError as exc:
        raise ValueError(f"Unknown configured subagent '{name}'.") from exc
    return agent_config["enabled"]


def load_nexus_config() -> dict[str, Any]:
    """Read the fail-fast typed Nexus runtime registry."""
    return load_validated_nexus_config()


def load_subagent_definition(name: str) -> SubAgentDefinition:
    """Read a project-scoped subagent AGENTS.md file with YAML frontmatter."""
    path = DEEPAGENTS_DIR / "agents" / name / "AGENTS.md"
    content = path.read_text(encoding="utf-8")
    metadata, system_prompt = _split_frontmatter(content, path)
    return SubAgentDefinition(
        name=metadata.get("name", name),
        description=metadata["description"],
        system_prompt=system_prompt.strip(),
        model=metadata.get("model"),
    )


def _split_frontmatter(content: str, path: Path) -> tuple[dict[str, str], str]:
    if not content.startswith("---\n"):
        msg = f"{path} must start with YAML frontmatter."
        raise ValueError(msg)

    try:
        _, frontmatter, body = content.split("---\n", 2)
    except ValueError as exc:
        msg = f"{path} has malformed YAML frontmatter."
        raise ValueError(msg) from exc

    metadata: dict[str, str] = {}
    for raw_line in frontmatter.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            msg = f"{path} frontmatter line is missing ':': {raw_line}"
            raise ValueError(msg)
        metadata[key.strip()] = value.strip().strip('"').strip("'")

    if "description" not in metadata:
        msg = f"{path} frontmatter must include description."
        raise ValueError(msg)
    return metadata, body


def _validated_skill_name(name: str) -> str:
    path = DEEPAGENTS_DIR / "skills" / name / "SKILL.md"
    if not path.is_file():
        msg = f"Configured skill '{name}' is missing at {path}."
        raise ValueError(msg)
    return name
