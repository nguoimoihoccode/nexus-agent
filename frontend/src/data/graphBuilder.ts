import {
  AGENT_NODE_IDS,
  AGENT_RESOURCE_NODES,
  BASE_NODES,
  BUILTIN_SKILL_NODES,
  ICON_BY_NAME,
} from "./nodes.js";
import { BASE_EDGES } from "./edges.js";
import type { GraphEdge, GraphNode, GraphPositions, NexusConfig, NexusSkillConfig, PositionedGraphNode } from "./types.js";

export { AGENT_NODE_IDS } from "./nodes.js";

const AGENT_NAMES = ["supervisor", ...AGENT_NODE_IDS];

export const FAIL_CLOSED_NEXUS_CONFIG: NexusConfig = {
  skills: {},
  resource_edges: [],
  agents: Object.fromEntries(AGENT_NAMES.map((name) => [name, {
    enabled: name === "supervisor",
    skills: [],
  }])),
};

function getAgentSkillNames(config: NexusConfig, agent: string): string[] {
  return config.agents[agent]?.skills || [];
}

function getAgentResourceNames(config: NexusConfig, agent: string): string[] {
  const generated = config.resource_edges.filter((edge) => edge.from === agent).map((edge) => edge.to);
  return generated.length ? generated : AGENT_RESOURCE_NODES[agent] || [];
}

function agentDetailSummary(config: NexusConfig, agent: string): string {
  const skillCount = getAgentSkillNames(config, agent).length;
  const resourceCount = getAgentResourceNames(config, agent).length;
  const parts: string[] = [];
  if (skillCount) parts.push(`${skillCount} skill${skillCount > 1 ? "s" : ""}`);
  if (resourceCount) parts.push(`${resourceCount} tool/resource${resourceCount > 1 ? "s" : ""}`);
  return parts.join(" · ") || "No hidden details";
}

export function skillNodeId(name: string): string {
  return `${name.replace(/[^a-zA-Z0-9_-]/g, "-")}-skill`;
}

function titleize(value: string): string {
  return value.replace(/[-_]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function normalizeSkills(value: unknown): Record<string, NexusSkillConfig> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).flatMap(([name, item]) => (
    item && typeof item === "object" && !Array.isArray(item)
      ? [[name, item as NexusSkillConfig]]
      : []
  )));
}

export function normalizeNexusConfig(config: unknown): NexusConfig {
  const root = config && typeof config === "object" && !Array.isArray(config)
    ? config as Record<string, unknown>
    : {};
  const agents = root.agents && typeof root.agents === "object" && !Array.isArray(root.agents)
    ? root.agents as Record<string, unknown>
    : {};
  const resourceEdges = Array.isArray(root.resource_edges)
    ? root.resource_edges.flatMap((edge) => {
      if (!edge || typeof edge !== "object" || Array.isArray(edge)) return [];
      const record = edge as Record<string, unknown>;
      return typeof record.from === "string" && typeof record.to === "string"
        ? [{ ...record, from: record.from, to: record.to }]
        : [];
    })
    : [];
  return {
    skills: normalizeSkills(root.skills),
    resource_edges: resourceEdges,
    agents: Object.fromEntries(AGENT_NAMES.map((name) => {
      const raw = agents[name];
      const incoming = raw && typeof raw === "object" && !Array.isArray(raw)
        ? raw as Record<string, unknown>
        : {};
      return [name, {
        enabled: typeof incoming.enabled === "boolean" ? incoming.enabled : name === "supervisor",
        skills: Array.isArray(incoming.skills)
          ? incoming.skills.filter((item): item is string => typeof item === "string")
          : [],
        ...(typeof incoming.title === "string" ? { title: incoming.title } : {}),
        ...(typeof incoming.description === "string" ? { description: incoming.description } : {}),
        ...(Array.isArray(incoming.tool_labels)
          ? { tool_labels: incoming.tool_labels.filter((item): item is string => typeof item === "string") }
          : {}),
      }];
    })),
  };
}

function configuredNodeIds(config: NexusConfig, expandedAgents: ReadonlySet<string>): Set<string> {
  const ids = new Set(["input", "prompt", "memory", "supervisor", "model", "local", "output", "checkpoint"]);
  for (const agent of AGENT_NODE_IDS) {
    if (!config.agents[agent]?.enabled) continue;
    ids.add(agent);
    if (expandedAgents.has(agent)) {
      for (const resource of getAgentResourceNames(config, agent)) ids.add(resource);
      for (const skill of getAgentSkillNames(config, agent)) ids.add(skillNodeId(skill));
    }
  }
  for (const skill of config.agents.supervisor?.skills || []) ids.add(skillNodeId(skill));
  return ids;
}

export function configuredSkillNames(config: NexusConfig): string[] {
  const names = new Set(config.agents.supervisor?.skills || []);
  for (const agent of AGENT_NODE_IDS) {
    if (!config.agents[agent]?.enabled) continue;
    for (const skill of config.agents[agent]?.skills || []) names.add(skill);
  }
  return [...names];
}

function buildSkillNode(name: string, index: number, config: NexusConfig): GraphNode {
  const builtin = BUILTIN_SKILL_NODES[name];
  const metadata = config.skills[name] || {};
  if (builtin) {
    return {
      ...builtin,
      title: metadata.title || builtin.title,
      subtitle: metadata.subtitle || builtin.subtitle,
      tone: metadata.tone || builtin.tone,
      detail: metadata.detail || builtin.detail,
      icon: (metadata.icon && ICON_BY_NAME[metadata.icon]) || builtin.icon,
    };
  }
  return {
    id: skillNodeId(name),
    title: metadata.title || `${titleize(name)} Skill`,
    subtitle: metadata.subtitle || `.deepagents/skills/${name}`,
    icon: (metadata.icon && ICON_BY_NAME[metadata.icon]) || "sparkles",
    x: typeof metadata.x === "number" && Number.isFinite(metadata.x) ? metadata.x : 76,
    y: typeof metadata.y === "number" && Number.isFinite(metadata.y) ? metadata.y : Math.min(86, 48 + index * 8),
    tone: metadata.tone || "pink",
    detail: metadata.detail || `Project skill '${name}' loaded from .deepagents/skills/${name}.`,
  };
}

export function buildRuntimeGraph(
  config: NexusConfig,
  expandedAgents: ReadonlySet<string> = new Set(),
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodeIds = configuredNodeIds(config, expandedAgents);
  const skillNodes = configuredSkillNames(config).map((name, index) => buildSkillNode(name, index, config));
  const nodesById = new Map<string, GraphNode>([...BASE_NODES, ...skillNodes].map((node) => [node.id, {
    ...node,
    ...(AGENT_NODE_IDS.includes(node.id)
      ? { expandable: true, detailSummary: agentDetailSummary(config, node.id) }
      : {}),
  }]));
  const nodes = [...nodesById.values()].filter((node) => nodeIds.has(node.id));
  const supervisorSkillEdges: GraphEdge[] = (config.agents.supervisor?.skills || [])
    .map((skill) => skillNodeId(skill))
    .map((nodeId, index) => ({
      id: `supervisor-${nodeId}`, from: "supervisor", to: nodeId, curve: index % 2 === 0 ? -12 : 12,
    }));
  const edges: GraphEdge[] = [...BASE_EDGES, ...supervisorSkillEdges]
    .filter((edge) => nodeIds.has(edge.from) && nodeIds.has(edge.to));
  for (const agent of AGENT_NODE_IDS) {
    if (!config.agents[agent]?.enabled) continue;
    for (const [index, skill] of (config.agents[agent]?.skills || []).entries()) {
      const to = skillNodeId(skill);
      const id = `${agent}-${to}`;
      if (!edges.some((edge) => edge.id === id)) edges.push({ id, from: agent, to, curve: index % 2 === 0 ? 0 : 12 });
    }
  }
  return { nodes, edges };
}

export function defaultPositions(nodes: PositionedGraphNode[]): GraphPositions {
  return Object.fromEntries(nodes.map((node) => [node.id, { x: node.x, y: node.y }]));
}

export function loadNodePositions(nodes: PositionedGraphNode[]): GraphPositions {
  const fallback = defaultPositions(nodes);
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem("nexus-node-positions") || "null");
    const saved = parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
    return Object.fromEntries(nodes.map((node) => {
      const position = saved[node.id];
      const record = position && typeof position === "object" ? position as Record<string, unknown> : {};
      return [node.id, typeof record.x === "number" && typeof record.y === "number"
        ? { x: record.x, y: record.y }
        : fallback[node.id] ?? { x: node.x, y: node.y }];
    }));
  } catch {
    return fallback;
  }
}

export function loadExpandedAgents(): Set<string> {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem("nexus-expanded-agents") || "null");
    return new Set(Array.isArray(saved)
      ? saved.filter((agent): agent is string => typeof agent === "string" && AGENT_NODE_IDS.includes(agent))
      : []);
  } catch {
    return new Set();
  }
}
