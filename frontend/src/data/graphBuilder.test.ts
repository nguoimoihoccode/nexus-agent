import assert from "node:assert/strict";
import { test } from "vitest";

import {
  FAIL_CLOSED_NEXUS_CONFIG,
  buildRuntimeGraph,
  normalizeNexusConfig,
} from "./graphBuilder.js";

const ENABLED_CONFIG = normalizeNexusConfig({
  agents: {
    supervisor: { enabled: true, skills: [] },
    researcher: { enabled: true, skills: ["research"] },
    "quant-data-agent": { enabled: true, skills: ["quant-data"] },
    "quant-researcher": { enabled: true, skills: ["quant-research"] },
    "ai-trader-agent": { enabled: true, skills: ["ai-trader"] },
  },
});

test("normalization fails closed when topology is unavailable", () => {
  const config = normalizeNexusConfig({});
  assert.equal(config.agents.supervisor.enabled, true);
  assert.deepEqual(config.agents["quant-data-agent"], { enabled: false, skills: [] });
  assert.deepEqual(config.agents["quant-researcher"], { enabled: false, skills: [] });
  assert.deepEqual(config.agents["ai-trader-agent"], { enabled: false, skills: [] });
  assert.deepEqual(config, FAIL_CLOSED_NEXUS_CONFIG);
});

test("builds quant nodes, resources and edges when enabled", () => {
  const graph = buildRuntimeGraph(ENABLED_CONFIG, new Set(["quant-researcher"]));
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const edgeIds = new Set(graph.edges.map((edge) => edge.id));

  assert(nodeIds.has("quant-researcher"));
  assert(nodeIds.has("quant-research-skill"));
  assert(nodeIds.has("qlib-worker"));
  assert(edgeIds.has("supervisor-quant-researcher"));
  assert(edgeIds.has("quant-researcher-qlib-worker"));
});

test("builds quant data nodes, resources and edges when enabled", () => {
  const graph = buildRuntimeGraph(ENABLED_CONFIG, new Set(["quant-data-agent"]));
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const edgeIds = new Set(graph.edges.map((edge) => edge.id));

  assert(nodeIds.has("quant-data-agent"));
  assert(nodeIds.has("quant-data-skill"));
  assert(nodeIds.has("openbb-ingestion"));
  assert(edgeIds.has("supervisor-quant-data-agent"));
  assert(edgeIds.has("quant-data-agent-openbb-ingestion"));
});

test("builds AI-Trader nodes, resources and edges when enabled", () => {
  const graph = buildRuntimeGraph(ENABLED_CONFIG, new Set(["ai-trader-agent"]));
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const edgeIds = new Set(graph.edges.map((edge) => edge.id));

  assert(nodeIds.has("ai-trader-agent"));
  assert(nodeIds.has("ai-trader-skill"));
  assert(nodeIds.has("ai-trader-api"));
  assert(edgeIds.has("supervisor-ai-trader-agent"));
  assert(edgeIds.has("ai-trader-agent-ai-trader-api"));
});

for (const [agent, skill, resource] of [
  ["quant-researcher", "quant-research-skill", "qlib-worker"],
  ["quant-data-agent", "quant-data-skill", "openbb-ingestion"],
  ["ai-trader-agent", "ai-trader-skill", "ai-trader-api"],
]) {
  test(`removes ${agent} topology when disabled`, () => {
    const config = normalizeNexusConfig({
      agents: { [agent]: { enabled: false, skills: [skill.replace(/-skill$/, "")] } },
    });
    const graph = buildRuntimeGraph(config, new Set([agent]));
    const nodeIds = new Set(graph.nodes.map((node) => node.id));

    assert(!nodeIds.has(agent));
    assert(!nodeIds.has(skill));
    assert(!nodeIds.has(resource));
  });
}
