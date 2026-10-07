"""Focused tests for public LangGraph entrypoint registration."""

import ast
import json
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
AGENT_MODULE = BACKEND_ROOT / "source" / "agents" / "agent.py"


class GraphEntrypointTests(unittest.TestCase):
    def test_default_langgraph_exposes_only_supervisor(self) -> None:
        config = json.loads((BACKEND_ROOT / "langgraph.json").read_text())

        self.assertEqual(set(config["graphs"]), {"supervisor"})

    def test_explicit_studio_config_exposes_enabled_subagents(self) -> None:
        config = json.loads((BACKEND_ROOT / "langgraph.studio.json").read_text())
        nexus = json.loads((BACKEND_ROOT / ".deepagents" / "nexus.json").read_text())

        enabled_agents = {
            name
            for name, agent_config in nexus["agents"].items()
            if name == "supervisor" or agent_config.get("enabled", True)
        }

        self.assertEqual(
            set(config["graphs"]),
            {
                "supervisor",
                "researcher",
                "quant-data-agent",
                "quant-researcher",
                "ai-trader-agent",
            },
        )
        self.assertNotIn("general-purpose", config["graphs"])
        self.assertEqual(set(config["graphs"]), enabled_agents)

    def test_langgraph_entrypoints_resolve_to_agent_module_functions(self) -> None:
        tree = ast.parse(AGENT_MODULE.read_text())
        function_names = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        }

        for config_name in ("langgraph.json", "langgraph.studio.json"):
            config = json.loads((BACKEND_ROOT / config_name).read_text())
            for target in config["graphs"].values():
                path, separator, function_name = target.partition(":")
                with self.subTest(config=config_name, target=target):
                    self.assertEqual(path, "./source/agents/agent.py")
                    self.assertEqual(separator, ":")
                    self.assertIn(function_name, function_names)


if __name__ == "__main__":
    unittest.main()
