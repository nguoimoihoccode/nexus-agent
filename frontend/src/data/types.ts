export type GraphNode = {
  id: string;
  title: string;
  subtitle: string;
  icon: string;
  x: number;
  y: number;
  tone: string;
  detail: string;
  large?: boolean;
  expandable?: boolean;
  detailSummary?: string;
};

export type PositionedGraphNode = Pick<GraphNode, "id" | "x" | "y">;
export type GraphEdge = { id: string; from: string; to: string; curve?: number };
export type GraphPosition = { x: number; y: number };
export type GraphPositions = Record<string, GraphPosition>;

export type NexusAgentConfig = {
  enabled: boolean;
  skills: string[];
  title?: string;
  description?: string;
  tool_labels?: string[];
};

export type NexusSkillConfig = {
  title?: string;
  subtitle?: string;
  tone?: string;
  detail?: string;
  icon?: string;
  x?: number;
  y?: number;
};

export type NexusConfig = {
  agents: Record<string, NexusAgentConfig>;
  skills: Record<string, NexusSkillConfig>;
  resource_edges: Array<{ from: string; to: string; [key: string]: unknown }>;
};

export type LiveStep = [nodeId: string, edgeId: string | null, label: string];
export type LiveGraph = { prompt: string; steps: LiveStep[] };
