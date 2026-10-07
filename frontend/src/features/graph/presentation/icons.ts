import {
  Bot,
  BrainCircuit,
  ChartNoAxesCombined,
  Database,
  FileCode2,
  MemoryStick,
  Megaphone,
  MessagesSquare,
  Network,
  RadioTower,
  Search,
  ServerCog,
  Sparkles,
  TerminalSquare,
  UserRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { GraphEdge, GraphNode } from "../../../data/types.js";
import type { PresentedGraphNode } from "./types.js";

const ICONS: Record<string, LucideIcon> = {
  bot: Bot,
  brain: BrainCircuit,
  chart: ChartNoAxesCombined,
  database: Database,
  fileCode: FileCode2,
  memory: MemoryStick,
  megaphone: Megaphone,
  messages: MessagesSquare,
  network: Network,
  broadcast: RadioTower,
  search: Search,
  server: ServerCog,
  sparkles: Sparkles,
  terminal: TerminalSquare,
  user: UserRound,
};

export function withGraphIcons(graph: { nodes: GraphNode[]; edges: GraphEdge[] }): {
  nodes: PresentedGraphNode[];
  edges: GraphEdge[];
} {
  return {
    ...graph,
    nodes: graph.nodes.map((node) => ({
      ...node,
      icon: ICONS[node.icon] || Sparkles,
    })),
  };
}
