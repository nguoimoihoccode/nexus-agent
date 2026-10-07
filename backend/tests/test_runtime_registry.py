"""Focused tests for the typed Nexus runtime registry."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from source.agents.config.registry import (
    BACKEND_ROOT,
    DEEPAGENTS_DIR,
    NEXUS_CONFIG_FILE,
    RegistryValidationError,
    validate_nexus_registry,
    validate_repository_registry,
    frontend_safe_topology,
)
from source.agents.config.harness import nexus_harness_profile
from source.agents.memory import MEMORY_TOOL_NAMES
from source.agents.sub_agents import (
    ai_trader_agent,
    quant_data_agent,
    quant_researcher,
    researcher,
)


class RuntimeRegistryTests(unittest.TestCase):
    def test_frontend_topology_excludes_runtime_security_and_prompt_details(self) -> None:
        topology = frontend_safe_topology(validate_nexus_registry())

        self.assertEqual(topology["schema_version"], "1")
        rendered = json.dumps(topology)
        for private_key in (
            "permission_profile",
            "interrupt_policy",
            "response_schema",
            "model",
            "prompt",
            "credential",
        ):
            self.assertNotIn(private_key, rendered)

    def setUp(self) -> None:
        self.raw = json.loads(NEXUS_CONFIG_FILE.read_text(encoding="utf-8"))

    def _write_config(self, directory: Path, payload: dict) -> Path:
        path = directory / "nexus.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _copy_repository_registry_surfaces(self, destination: Path) -> Path:
        backend = destination / "backend"
        backend.mkdir(parents=True)
        for relative in ("langgraph.json", "langgraph.studio.json", "Dockerfile"):
            shutil.copy2(BACKEND_ROOT / relative, backend / relative)
        return backend

    def test_repository_registry_surfaces_are_consistent(self) -> None:
        registry = validate_repository_registry()

        self.assertEqual(
            set(registry.agents),
            {
                "supervisor",
                "researcher",
                "quant-data-agent",
                "quant-researcher",
                "ai-trader-agent",
            },
        )
        self.assertFalse((DEEPAGENTS_DIR / "AGENTS.md").exists())

    def test_harness_excludes_shell_and_all_filesystem_tools(self) -> None:
        self.assertEqual(
            nexus_harness_profile().excluded_tools,
            frozenset(
                {
                    "execute",
                    "write_file",
                    "edit_file",
                    "ls",
                    "read_file",
                    "glob",
                    "grep",
                }
            ),
        )

    def test_registry_tool_assignments_match_python_subagents(self) -> None:
        registry = validate_nexus_registry()
        self.assertEqual(set(registry.agents["supervisor"].tools), MEMORY_TOOL_NAMES)
        subagents = {
            "researcher": researcher,
            "quant-data-agent": quant_data_agent,
            "quant-researcher": quant_researcher,
            "ai-trader-agent": ai_trader_agent,
        }

        for name, subagent in subagents.items():
            with self.subTest(agent=name):
                self.assertEqual(
                    registry.agents[name].tools,
                    [tool.name for tool in subagent["tools"]],
                )

    def test_duplicate_registry_key_is_rejected(self) -> None:
        duplicate = (
            '{"skills": {}, "agents": {'
            '"researcher": {}, "researcher": {}}}'
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nexus.json"
            path.write_text(duplicate, encoding="utf-8")

            with self.assertRaisesRegex(RegistryValidationError, "Duplicate"):
                validate_nexus_registry(path, deepagents_dir=DEEPAGENTS_DIR)

    def test_unknown_skill_and_obsolete_policy_metadata_are_rejected(self) -> None:
        invalid_payloads = []
        unknown_skill = json.loads(json.dumps(self.raw))
        unknown_skill["agents"]["researcher"]["skills"].append("missing-skill")
        invalid_payloads.append(unknown_skill)

        obsolete_metadata = json.loads(json.dumps(self.raw))
        obsolete_metadata["agents"]["quant-data-agent"][
            "permission_profile"
        ] = "domain-read-only"
        invalid_payloads.append(obsolete_metadata)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for index, payload in enumerate(invalid_payloads):
                with self.subTest(index=index):
                    path = self._write_config(directory, payload)
                    with self.assertRaises(RegistryValidationError):
                        validate_nexus_registry(path, deepagents_dir=DEEPAGENTS_DIR)

    def test_missing_prompt_and_mismatched_skill_frontmatter_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / ".deepagents"
            shutil.copytree(DEEPAGENTS_DIR, copied)
            (copied / "agents" / "researcher" / "AGENTS.md").unlink()

            with self.assertRaisesRegex(RegistryValidationError, "missing"):
                validate_nexus_registry(copied / "nexus.json", deepagents_dir=copied)

        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / ".deepagents"
            shutil.copytree(DEEPAGENTS_DIR, copied)
            skill = copied / "skills" / "research" / "SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "name: research",
                    "name: wrong-name",
                    1,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RegistryValidationError, "frontmatter name"):
                validate_nexus_registry(copied / "nexus.json", deepagents_dir=copied)

    def test_repository_validation_rejects_production_graph_drift(self) -> None:
        registry = validate_nexus_registry()

        with tempfile.TemporaryDirectory() as temporary:
            backend = self._copy_repository_registry_surfaces(Path(temporary))
            dockerfile = backend / "Dockerfile"
            dockerfile.write_text(
                dockerfile.read_text(encoding="utf-8").replace(
                    '{"supervisor": "/deps/backend/source/agents/agent.py:graph"}',
                    '{"supervisor": "/deps/backend/source/agents/agent.py:graph", '
                    '"researcher": "/deps/backend/source/agents/agent.py:researcher_graph"}',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RegistryValidationError, "only the supervisor"):
                validate_repository_registry(registry, backend_root=backend)

    def test_repository_validation_rejects_default_and_studio_graph_drift(self) -> None:
        registry = validate_nexus_registry()

        with tempfile.TemporaryDirectory() as temporary:
            backend = self._copy_repository_registry_surfaces(Path(temporary))
            default = backend / "langgraph.json"
            config = json.loads(default.read_text(encoding="utf-8"))
            config["graphs"]["researcher"] = (
                "./source/agents/agent.py:researcher_graph"
            )
            default.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(RegistryValidationError, "only the supervisor"):
                validate_repository_registry(registry, backend_root=backend)

        with tempfile.TemporaryDirectory() as temporary:
            backend = self._copy_repository_registry_surfaces(Path(temporary))
            studio = backend / "langgraph.studio.json"
            config = json.loads(studio.read_text(encoding="utf-8"))
            del config["graphs"]["researcher"]
            studio.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(RegistryValidationError, "Studio graph"):
                validate_repository_registry(registry, backend_root=backend)

    def test_repository_validation_rejects_product_http_security_drift(self) -> None:
        registry = validate_nexus_registry()

        with tempfile.TemporaryDirectory() as temporary:
            backend = self._copy_repository_registry_surfaces(Path(temporary))
            langgraph = backend / "langgraph.json"
            config = json.loads(langgraph.read_text(encoding="utf-8"))
            config["http"]["enable_custom_route_auth"] = False
            langgraph.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(RegistryValidationError, "Development HTTP"):
                validate_repository_registry(
                    registry,
                    backend_root=backend,
                )

        with tempfile.TemporaryDirectory() as temporary:
            backend = self._copy_repository_registry_surfaces(Path(temporary))
            dockerfile = backend / "Dockerfile"
            dockerfile.write_text(
                dockerfile.read_text(encoding="utf-8").replace(
                    '"enable_custom_route_auth": true',
                    '"enable_custom_route_auth": false',
                    1,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RegistryValidationError, "Production HTTP"):
                validate_repository_registry(
                    registry,
                    backend_root=backend,
                )

        with tempfile.TemporaryDirectory() as temporary:
            backend = self._copy_repository_registry_surfaces(Path(temporary))
            dockerfile = backend / "Dockerfile"
            dockerfile.write_text(
                dockerfile.read_text(encoding="utf-8").replace(
                    '"pickle_fallback": false',
                    '"pickle_fallback": true',
                    1,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RegistryValidationError, "Production checkpointer"):
                validate_repository_registry(
                    registry,
                    backend_root=backend,
                )


if __name__ == "__main__":
    unittest.main()
