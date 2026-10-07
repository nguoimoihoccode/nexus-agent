"""Typed Nexus registry validation without loading models or infrastructure."""

import json
import re
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEEPAGENTS_DIR = BACKEND_ROOT / ".deepagents"
NEXUS_CONFIG_FILE = DEEPAGENTS_DIR / "nexus.json"

AGENT_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"
SKILL_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,63}$"
TOOL_NAME_PATTERN = r"^[a-zA-Z0-9_]{1,96}$"

AGENT_MODULES = {
    "supervisor": None,
    "researcher": "researcher",
    "quant-data-agent": "quant_data_agent",
    "quant-researcher": "quant_researcher",
    "ai-trader-agent": "ai_trader_agent",
}

PRESENTATION_AGENT_METADATA = {
    "supervisor": {
        "title": "Supervisor Agent",
        "description": "Coordinates governed requests, approvals, and durable runs.",
    },
    "researcher": {
        "title": "Researcher",
        "description": "Retrieves and records source-backed research evidence.",
    },
    "quant-data-agent": {
        "title": "Quant Data Agent",
        "description": "Builds immutable, validated market-data revisions.",
    },
    "quant-researcher": {
        "title": "Quant Researcher",
        "description": "Runs governed experiments and records evidence-linked interpretation.",
    },
    "ai-trader-agent": {
        "title": "AI-Trader Agent",
        "description": "Reads market context and publishes only through approval.",
    },
}

PRESENTATION_RESOURCES = {
    "researcher": "web-search",
    "quant-data-agent": "openbb-ingestion",
    "quant-researcher": "qlib-worker",
    "ai-trader-agent": "ai-trader-api",
}


class RegistryValidationError(ValueError):
    """Raised when a registry or one of its runtime surfaces drifts."""


class RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SkillMetadata(RegistryModel):
    title: str = Field(min_length=1, max_length=160)
    tone: str = Field(min_length=1, max_length=32)
    icon: str = Field(min_length=1, max_length=64)
    subtitle: str | None = Field(default=None, max_length=200)
    detail: str | None = Field(default=None, max_length=1000)
    x: float | int | None = None
    y: float | int | None = None


class AgentRegistryEntry(RegistryModel):
    enabled: bool = True
    skills: list[str]
    tools: list[str]

    @field_validator("skills", "tools")
    @classmethod
    def reject_duplicate_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Registry lists cannot contain duplicate values.")
        return values

    @field_validator("tools")
    @classmethod
    def validate_tool_names(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(TOOL_NAME_PATTERN, value) for value in values):
            raise ValueError("Registry tool names must be identifier-like values.")
        return values


class NexusRegistry(RegistryModel):
    skills: dict[str, SkillMetadata]
    agents: dict[str, AgentRegistryEntry]

    @model_validator(mode="after")
    def validate_names_and_references(self) -> Self:
        expected_agents = set(AGENT_MODULES)
        actual_agents = set(self.agents)
        if actual_agents != expected_agents:
            unknown = sorted(actual_agents - expected_agents)
            missing = sorted(expected_agents - actual_agents)
            raise ValueError(f"Agent registry drift: unknown={unknown}, missing={missing}.")
        if not self.agents["supervisor"].enabled:
            raise ValueError("The supervisor must remain enabled.")

        for skill_name in self.skills:
            if not re.fullmatch(SKILL_NAME_PATTERN, skill_name):
                raise ValueError(f"Invalid skill name '{skill_name}'.")
        for agent_name, entry in self.agents.items():
            if not re.fullmatch(AGENT_NAME_PATTERN, agent_name):
                raise ValueError(f"Invalid agent name '{agent_name}'.")
            missing_skills = sorted(set(entry.skills) - set(self.skills))
            if missing_skills:
                raise ValueError(
                    f"Agent '{agent_name}' references unknown skills {missing_skills}."
                )
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryValidationError(f"Duplicate registry key '{key}'.")
        result[key] = value
    return result


def _read_frontmatter(path: Path) -> dict[str, str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryValidationError(f"Required harness file is missing: {path}.") from exc
    if not content.startswith("---\n"):
        raise RegistryValidationError(f"{path} must start with YAML frontmatter.")
    try:
        _, frontmatter, _body = content.split("---\n", 2)
    except ValueError as exc:
        raise RegistryValidationError(f"{path} has malformed YAML frontmatter.") from exc

    metadata: dict[str, str] = {}
    for raw_line in frontmatter.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise RegistryValidationError(
                f"{path} frontmatter line is missing ':': {raw_line}"
            )
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata


def _validate_harness_files(registry: NexusRegistry, deepagents_dir: Path) -> None:
    skill_dirs = {
        path.parent.name
        for path in (deepagents_dir / "skills").glob("*/SKILL.md")
        if path.is_file()
    }
    if skill_dirs != set(registry.skills):
        raise RegistryValidationError(
            "Skill registry drift: "
            f"configured={sorted(registry.skills)}, files={sorted(skill_dirs)}."
        )
    for skill_name in registry.skills:
        path = deepagents_dir / "skills" / skill_name / "SKILL.md"
        metadata = _read_frontmatter(path)
        if metadata.get("name") != skill_name:
            raise RegistryValidationError(
                f"Skill '{skill_name}' frontmatter name is {metadata.get('name')!r}."
            )
        if not metadata.get("description"):
            raise RegistryValidationError(
                f"Skill '{skill_name}' frontmatter must include description."
            )

    for agent_name, module in AGENT_MODULES.items():
        if module is None:
            continue
        prompt_path = deepagents_dir / "agents" / agent_name / "AGENTS.md"
        metadata = _read_frontmatter(prompt_path)
        if metadata.get("name") != agent_name:
            raise RegistryValidationError(
                f"Agent '{agent_name}' frontmatter name is {metadata.get('name')!r}."
            )
        module_source = (
            BACKEND_ROOT / "source" / "agents" / "sub_agents" / module / "__init__.py"
        )
        if not module_source.is_file():
            raise RegistryValidationError(
                f"Agent '{agent_name}' Python module is missing: {module_source}."
            )
        expected_loader = f'load_subagent_definition("{agent_name}")'
        if expected_loader not in module_source.read_text(encoding="utf-8"):
            raise RegistryValidationError(
                f"Agent '{agent_name}' Python module does not load its prompt definition."
            )


def validate_nexus_registry(
    config_path: Path = NEXUS_CONFIG_FILE,
    *,
    deepagents_dir: Path = DEEPAGENTS_DIR,
) -> NexusRegistry:
    """Validate registry shape and local prompt/skill definitions."""
    try:
        raw = json.loads(
            config_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except RegistryValidationError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryValidationError(f"Unable to read registry {config_path}.") from exc
    try:
        registry = NexusRegistry.model_validate(raw)
    except ValidationError as exc:
        raise RegistryValidationError(f"Invalid Nexus registry: {exc}") from exc
    _validate_harness_files(registry, deepagents_dir)
    return registry


def _docker_json_environment(dockerfile: str, variable: str) -> dict[str, Any]:
    match = re.search(rf"{re.escape(variable)}='([^']+)'", dockerfile)
    if not match:
        raise RegistryValidationError(f"Production {variable} is missing.")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RegistryValidationError(f"Production {variable} is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise RegistryValidationError(f"Production {variable} must be a JSON object.")
    return value


def _validate_http_runtime(config: object, *, app: str, surface: str) -> None:
    required = {
        "app": app,
        "enable_custom_route_auth": True,
        "middleware_order": "auth_first",
        "disable_mcp": True,
        "disable_a2a": True,
        "disable_webhooks": True,
    }
    if not isinstance(config, dict) or any(
        config.get(key) != expected for key, expected in required.items()
    ):
        raise RegistryValidationError(
            f"{surface} HTTP runtime must mount the product app auth-first and "
            "disable unused protocol surfaces."
        )


def _validate_checkpointer_runtime(config: object, *, surface: str) -> None:
    if not isinstance(config, dict):
        raise RegistryValidationError(f"{surface} checkpointer policy is missing.")
    if config.get("ttl") != {
        "strategy": "delete",
        "sweep_interval_minutes": 60,
        "default_ttl": 43_200,
    } or config.get("serde") != {"allowed_json_modules": [], "pickle_fallback": False}:
        raise RegistryValidationError(
            f"{surface} checkpointer must enforce bounded TTL and safe serialization."
        )


def validate_repository_registry(
    registry: NexusRegistry | None = None,
    *,
    backend_root: Path = BACKEND_ROOT,
) -> NexusRegistry:
    """Cross-check registry with development and production runtime exposure."""
    registry = registry or validate_nexus_registry()
    enabled_graphs = {name for name, entry in registry.agents.items() if entry.enabled}

    langgraph = json.loads((backend_root / "langgraph.json").read_text(encoding="utf-8"))
    if set(langgraph.get("graphs", {})) != {"supervisor"}:
        raise RegistryValidationError(
            "Default development runtime must expose only the supervisor graph."
        )
    if langgraph.get("auth") != {"path": "./source/security/auth.py:auth"}:
        raise RegistryValidationError(
            "Development authentication entrypoint does not match Nexus security policy."
        )
    _validate_http_runtime(
        langgraph.get("http"),
        app="./source/http_app.py:app",
        surface="Development",
    )
    _validate_checkpointer_runtime(langgraph.get("checkpointer"), surface="Development")

    studio = json.loads(
        (backend_root / "langgraph.studio.json").read_text(encoding="utf-8")
    )
    if set(studio.get("graphs", {})) != enabled_graphs:
        raise RegistryValidationError(
            "Explicit Studio graph exposure does not match enabled Nexus agents."
        )
    if studio.get("auth") != {"path": "./source/security/auth.py:auth"}:
        raise RegistryValidationError(
            "Studio authentication entrypoint does not match Nexus security policy."
        )
    _validate_http_runtime(
        studio.get("http"),
        app="./source/http_app.py:app",
        surface="Studio",
    )
    _validate_checkpointer_runtime(studio.get("checkpointer"), surface="Studio")

    dockerfile = (backend_root / "Dockerfile").read_text(encoding="utf-8")
    if "LANGGRAPH_STRICT_MSGPACK='true'" not in dockerfile:
        raise RegistryValidationError(
            "Production LangGraph msgpack deserialization must be strict."
        )
    production_config = _docker_json_environment(dockerfile, "LANGSERVE_GRAPHS")
    if set(production_config) != {"supervisor"}:
        raise RegistryValidationError(
            "Production must expose only the supervisor graph."
        )
    production_auth = _docker_json_environment(dockerfile, "LANGGRAPH_AUTH")
    if production_auth != {"path": "/deps/backend/source/security/auth.py:auth"}:
        raise RegistryValidationError(
            "Production authentication entrypoint does not match Nexus security policy."
        )
    _validate_http_runtime(
        _docker_json_environment(dockerfile, "LANGGRAPH_HTTP"),
        app="/deps/backend/source/http_app.py:app",
        surface="Production",
    )
    _validate_checkpointer_runtime(
        _docker_json_environment(dockerfile, "LANGGRAPH_CHECKPOINTER"),
        surface="Production",
    )
    return registry


def load_validated_nexus_config() -> dict[str, Any]:
    """Return a JSON-compatible registry after fail-fast validation."""
    return validate_nexus_registry().model_dump(mode="json")


def frontend_safe_topology(registry: NexusRegistry | None = None) -> dict[str, Any]:
    """Project the registry without prompts, permissions, or callables."""
    active = registry or validate_nexus_registry()
    return {
        "schema_version": "1",
        "agents": {
            name: {
                "enabled": entry.enabled,
                "skills": list(entry.skills),
                "tool_labels": [tool.replace("_", " ") for tool in entry.tools],
                **PRESENTATION_AGENT_METADATA[name],
            }
            for name, entry in active.agents.items()
        },
        "skills": {
            name: metadata.model_dump(mode="json", exclude_none=True)
            for name, metadata in active.skills.items()
        },
        "resource_edges": [
            {"from": name, "to": resource, "kind": "uses"}
            for name, resource in PRESENTATION_RESOURCES.items()
            if active.agents[name].enabled
        ],
    }
