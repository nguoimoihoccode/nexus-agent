import { AGENT_NODE_IDS, skillNodeId } from "../../../data/graphBuilder.js";
import type { StreamActivity } from "../../../types/contracts.js";
import type { LiveStep, NexusConfig } from "../../../data/types.js";

type AgentEdges = {
  supervisorToAgent: string;
  agentToSupervisor: string;
  agentToModel: string;
  modelToAgent: string;
  agentToSkill: string;
};

function agentEdges(agentNode: string | null, activeSkillNode: string | null): AgentEdges | null {
  const defaults: Record<string, AgentEdges> = {
    researcher: {
      supervisorToAgent: "supervisor-researcher",
      agentToSupervisor: "researcher-supervisor",
      agentToModel: "researcher-model",
      modelToAgent: "model-researcher",
      agentToSkill: activeSkillNode ? `researcher-${activeSkillNode}` : "researcher-skill",
    },
    "ai-trader-agent": {
      supervisorToAgent: "supervisor-ai-trader-agent",
      agentToSupervisor: "ai-trader-agent-supervisor",
      agentToModel: "ai-trader-agent-model",
      modelToAgent: "model-ai-trader-agent",
      agentToSkill: activeSkillNode
        ? `ai-trader-agent-${activeSkillNode}`
        : "ai-trader-agent-skill",
    },
    "quant-data-agent": {
      supervisorToAgent: "supervisor-quant-data-agent",
      agentToSupervisor: "quant-data-agent-supervisor",
      agentToModel: "quant-data-agent-model",
      modelToAgent: "model-quant-data-agent",
      agentToSkill: activeSkillNode
        ? `quant-data-agent-${activeSkillNode}`
        : "quant-data-agent-skill",
    },
    "quant-researcher": {
      supervisorToAgent: "supervisor-quant-researcher",
      agentToSupervisor: "quant-researcher-supervisor",
      agentToModel: "quant-researcher-model",
      modelToAgent: "model-quant-researcher",
      agentToSkill: activeSkillNode
        ? `quant-researcher-${activeSkillNode}`
        : "quant-researcher-skill",
    },
  };
  return agentNode ? defaults[agentNode] ?? null : null;
}

export function projectActivity(label: string, activity: StreamActivity, nexusConfig: NexusConfig): {
  agentNode: string | null;
  activeSkill: string | null;
  activeSkillNode: string | null;
  initialSkillStep: LiveStep | null;
  steps: LiveStep[];
} {
  const reportedAgent = activity.agentName;
  const agentNode = typeof reportedAgent === "string" && AGENT_NODE_IDS.includes(reportedAgent)
    ? reportedAgent
    : null;
  if (activity.kind === "graph_update" || (!agentNode && activity.isSubagent)) {
    return { agentNode, activeSkill: null, activeSkillNode: null, initialSkillStep: null, steps: [] };
  }

  const configuredSkills = agentNode ? nexusConfig.agents[agentNode]?.skills || [] : [];
  const reportedSkill = activity.skillName;
  const activeSkill = typeof reportedSkill === "string" && configuredSkills.includes(reportedSkill)
    ? reportedSkill
    : configuredSkills[0] ?? null;
  const activeSkillNode = activeSkill ? skillNodeId(activeSkill) : null;
  const edges = agentEdges(agentNode, activeSkillNode);
  const steps: LiveStep[] = [];

  if (activity.kind === "subagent_complete" && edges) {
    steps.push(["supervisor", edges.agentToSupervisor, label]);
  } else if (activity.kind === "tool_result") {
    if (activity.memoryWriteResponse) {
      steps.push(["supervisor", "memory-supervisor", label]);
    }
  } else if (activity.kind === "tool_call") {
    if (activity.toolName === "task" && edges) {
      steps.push(["supervisor", "model-supervisor", "Supervisor nhận delegation intent từ model"]);
      if (agentNode) steps.push([agentNode, edges.supervisorToAgent, `Supervisor giao việc cho ${agentNode}`]);
    } else if (edges) {
      if (agentNode) steps.push([agentNode, edges.modelToAgent, `${agentNode} nhận tool intent từ model`]);
    } else {
      steps.push(["supervisor", "model-supervisor", "Supervisor nhận tool intent từ model"]);
    }
    if (activity.skillName && edges && activeSkillNode) {
      steps.push([
        activeSkillNode,
        edges.agentToSkill,
        `${agentNode} đọc ${activity.skillName} skill instructions`,
      ]);
    } else if (agentNode === "researcher" && activity?.toolName === "web_search") {
      steps.push(["web-search", "researcher-web-search", label]);
    } else if (agentNode === "ai-trader-agent" && activity?.toolName) {
      steps.push(["ai-trader-api", "ai-trader-agent-ai-trader-api", label]);
    } else if (agentNode === "quant-data-agent" && activity?.toolName) {
      steps.push(["openbb-ingestion", "quant-data-agent-openbb-ingestion", label]);
    } else if (agentNode === "quant-researcher" && activity?.toolName) {
      steps.push(["qlib-worker", "quant-researcher-qlib-worker", label]);
    } else if (!agentNode && activity.toolName && ["save_user_memory", "delete_user_memory"].includes(activity.toolName)) {
      steps.push(["memory", "supervisor-memory", "Supervisor cập nhật persistent memory"]);
    }
  } else if (activity.kind === "model_stream" && edges) {
    steps.push(["model", edges.agentToModel, label]);
  } else if (activity.kind === "model_stream") {
    steps.push(["model", "supervisor-model", label]);
  }

  return {
    agentNode,
    activeSkill,
    activeSkillNode,
    initialSkillStep: edges && activeSkillNode
      ? [activeSkillNode, edges.agentToSkill, `${agentNode} nạp ${activeSkill} skill instructions`]
      : null,
    steps,
  };
}
