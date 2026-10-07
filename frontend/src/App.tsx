import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  BrainCircuit,
  FileSearch,
  MessageSquare,
  Network,
  RotateCcw,
  Settings,
  Wifi,
  WifiOff,
} from "lucide-react";

import { fetchProductTopology } from "./api/governedQuant.js";
import { WorkspaceGraph } from "./components/graph/WorkspaceGraph.js";
import { withGraphIcons } from "./features/graph/presentation/icons.js";
import {
  AGENT_NODE_IDS,
  FAIL_CLOSED_NEXUS_CONFIG,
  buildRuntimeGraph,
  configuredSkillNames,
  loadExpandedAgents,
  normalizeNexusConfig,
} from "./data/graphBuilder.js";
import { useBackendHealth } from "./hooks/useBackendHealth.js";
import { useChat } from "./hooks/useChat.js";
import { useNodeDrag } from "./hooks/useNodeDrag.js";
import { edgePath } from "./utils/graphLayout.js";

const NAV_ITEMS = [
  { to: "/chat", label: "Trò chuyện", icon: MessageSquare },
  { to: "/evidence", label: "Experiments", icon: FileSearch },
  { to: "/agents", label: "Capabilities", icon: Network },
  { to: "/settings", label: "Cài đặt", icon: Settings },
] as const;

function pageTitle(pathname: string) {
  if (pathname.startsWith("/evidence")) return "Experiments";
  if (pathname.startsWith("/agents")) return "Capabilities";
  if (pathname.startsWith("/settings")) return "Cài đặt";
  return "Trung tâm điều phối";
}

function useWorkspaceState() {
  const topology = useQuery({
    queryKey: ["product-topology"],
    queryFn: ({ signal }) => fetchProductTopology(signal),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const nexusConfig = useMemo(
    () => normalizeNexusConfig(topology.data ?? FAIL_CLOSED_NEXUS_CONFIG),
    [topology.data],
  );
  const [expandedAgents, setExpandedAgents] = useState<Set<string>>(loadExpandedAgents);
  const { nodes, edges } = useMemo(
    () => withGraphIcons(buildRuntimeGraph(nexusConfig, expandedAgents)),
    [nexusConfig, expandedAgents],
  );
  const [graphFilter, setGraphFilter] = useState<"agents" | "resources" | "all">("agents");
  const [selected, setSelected] = useState(() => (
    nodes.find((node) => node.id === "supervisor") ?? null
  ));
  const [canvasSize, setCanvasSize] = useState({ width: 1200, height: 650 });
  const [edgePaths, setEdgePaths] = useState<Record<string, string>>({});
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [canvasElement, setCanvasElement] = useState<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLElement>());
  const bindCanvas = useCallback((element: HTMLDivElement | null) => {
    canvasRef.current = element;
    setCanvasElement(element);
  }, []);

  const chat = useChat(nexusConfig, { setExpandedAgents });
  const backendHealth = useBackendHealth();
  const drag = useNodeDrag(canvasRef, nodeRefs, nodes);
  const visibleNodeIds = useMemo(() => new Set(nodes.map((node) => node.id)), [nodes]);
  const visibleEdgeIds = useMemo(() => new Set(edges.map((edge) => edge.id)), [edges]);
  const current = useMemo(() => ({
    prompt: chat.livePrompt,
    steps: chat.liveSteps.filter(([nodeId, edgeId]) => (
      visibleNodeIds.has(nodeId) && (!edgeId || visibleEdgeIds.has(edgeId))
    )),
  }), [chat.livePrompt, chat.liveSteps, visibleEdgeIds, visibleNodeIds]);
  const graphStep = current.steps.length - 1;
  const skillCount = useMemo(() => configuredSkillNames(nexusConfig).length, [nexusConfig]);
  const enabledAgents = useMemo(
    () => Object.keys(nexusConfig.agents)
      .filter((agent) => nexusConfig.agents[agent]?.enabled !== false),
    [nexusConfig],
  );
  const agentRows = useMemo(() => Object.keys(nexusConfig.agents).map((agentId) => {
    const node = nodes.find((item) => item.id === agentId);
    const agentConfig = nexusConfig.agents[agentId];
    return {
      id: agentId,
      title: agentConfig?.title || node?.title || agentId,
      detail: agentConfig?.description || node?.detail || "",
      tone: node?.tone || "green",
      icon: node?.icon || Bot,
      enabled: agentId === "supervisor" || agentConfig?.enabled !== false,
      skills: agentConfig?.skills || [],
      toolLabels: agentConfig?.tool_labels || [],
      resources: [...new Set(nexusConfig.resource_edges
        .filter((edge) => edge.from === agentId)
        .map((edge) => edge.to))],
    };
  }), [nexusConfig, nodes]);

  useEffect(() => {
    setSelected((previous) => {
      if (previous && nodes.some((node) => node.id === previous.id)) return previous;
      return nodes.find((node) => node.id === "supervisor") ?? nodes[0] ?? null;
    });
  }, [nodes]);

  useEffect(() => {
    if (!canvasElement) return undefined;
    const update = () => setCanvasSize({
      width: canvasElement.clientWidth,
      height: canvasElement.clientHeight,
    });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(canvasElement);
    return () => observer.disconnect();
  }, [canvasElement]);

  useLayoutEffect(() => {
    if (!canvasElement) return;
    setEdgePaths(Object.fromEntries(edges.map((edge) => [
      edge.id,
      edgePath(
        nodeRefs.current.get(edge.from),
        nodeRefs.current.get(edge.to),
        canvasElement,
        edge.curve,
      ),
    ])));
  }, [canvasElement, canvasSize, drag.nodePositions, edges, graphFilter]);

  const visitedNodes = useMemo(
    () => new Set(current.steps.map(([node]) => node)),
    [current.steps],
  );
  const visitedEdges = useMemo(
    () => new Set(current.steps.map(([, edge]) => edge).filter((edge): edge is string => Boolean(edge))),
    [current.steps],
  );
  const activeNode = chat.chatting && graphStep >= 0 ? current.steps[graphStep]?.[0] ?? null : null;
  const activeEdge = chat.chatting && graphStep >= 0 ? current.steps[graphStep]?.[1] ?? null : null;

  const toggleAgentDetails = useCallback((agent: string) => {
    if (!AGENT_NODE_IDS.includes(agent)) return;
    setExpandedAgents((items) => {
      const next = new Set(items);
      if (next.has(agent)) next.delete(agent);
      else next.add(agent);
      window.localStorage.setItem("nexus-expanded-agents", JSON.stringify([...next]));
      return next;
    });
  }, []);

  const selectNode = useCallback((nodeId: string) => {
    const node = nodes.find((item) => item.id === nodeId);
    if (node) setSelected(node);
  }, [nodes]);

  const graph = (
    <WorkspaceGraph
      canvasRef={bindCanvas}
      canvasSize={canvasSize}
      current={current}
      edges={edges}
      nodes={nodes}
      edgePaths={edgePaths}
      visitedEdges={visitedEdges}
      activeEdge={activeEdge}
      activeNode={activeNode}
      visitedNodes={visitedNodes}
      selected={selected}
      draggingNode={drag.draggingNode}
      nodePositions={drag.nodePositions}
      expandedAgents={expandedAgents}
      suppressNodeClickRef={drag.suppressNodeClickRef}
      nodeRefs={nodeRefs}
      onResetNodeLayout={drag.resetNodeLayout}
      onSetSelected={setSelected}
      onToggleAgentDetails={toggleAgentDetails}
      onStartNodeDrag={drag.startNodeDrag}
      onMoveNode={drag.moveNode}
      onEndNodeDrag={drag.endNodeDrag}
      filter={graphFilter}
      onSetFilter={setGraphFilter}
    />
  );

  return {
    activeNode,
    agentRows,
    backendHealth,
    chat,
    current,
    enabledAgents,
    expandedAgents,
    graph,
    nexusConfig,
    nodes,
    selectNode,
    selected,
    setSelected,
    skillCount,
    toggleAgentDetails,
    topology,
    visitedNodes,
  };
}

type WorkspaceState = ReturnType<typeof useWorkspaceState>;
const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside the Nexus app shell.");
  return value;
}

function ProductShell({ children }: { children: ReactNode }) {
  const workspace = useWorkspace();
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  useEffect(() => {
    document.querySelector(".product-main")?.scrollTo({ top: 0, left: 0, behavior: "auto" });
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [pathname]);

  return (
    <div className="product-shell">
      <aside className="sidebar">
        <Link className="brand-lockup" to="/chat">
          <span className="brand-mark"><BrainCircuit size={20} /></span>
          <span><strong>Nexus</strong><small>Agent Console</small></span>
        </Link>

        <nav className="side-nav" aria-label="Primary">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                activeOptions={{ includeSearch: false }}
                activeProps={{ className: "active" }}
                aria-label={item.label}
                title={item.label}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-status">
          <span className={workspace.chat.chatting ? "status-dot running" : "status-dot"} />
          <div>
            <strong>{workspace.chat.chatting ? "Đang chạy" : "Sẵn sàng"}</strong>
            <small>{workspace.enabledAgents.length} agents · {workspace.skillCount} skills</small>
          </div>
        </div>
      </aside>

      <div className="product-main">
        <header className="product-topbar">
          <div><span className="topbar-kicker">Không gian làm việc</span><h1>{pageTitle(pathname)}</h1></div>
          <div className="topbar-actions">
            <span className="env-badge">Cục bộ</span>
            <button
              type="button"
              className={`health-badge ${workspace.backendHealth.status}`}
              onClick={workspace.backendHealth.refresh}
              title="Kiểm tra lại kết nối backend"
            >
              {workspace.backendHealth.status === "offline" ? <WifiOff size={14} /> : <Wifi size={14} />}
              <span className="health-label">
                {workspace.backendHealth.status === "online" ? "Backend đã kết nối" : workspace.backendHealth.status === "offline" ? "Backend ngoại tuyến" : "Đang kiểm tra"}
              </span>
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={workspace.chat.clearChat}
              title="Tạo cuộc trò chuyện mới"
              aria-label="Tạo cuộc trò chuyện mới"
            >
              <RotateCcw size={17} />
            </button>
          </div>
        </header>

        <main className="product-content">{children}</main>
      </div>
    </div>
  );
}

export default function App() {
  const workspace = useWorkspaceState();
  return (
    <WorkspaceContext.Provider value={workspace}>
      <ProductShell><Outlet /></ProductShell>
    </WorkspaceContext.Provider>
  );
}
