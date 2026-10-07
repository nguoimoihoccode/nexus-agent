import {
  type Dispatch,
  type PointerEvent,
  type RefObject,
  type SetStateAction,
  useRef,
  useState,
} from "react";
import { Activity, Focus, Minus, Plus, RotateCcw } from "lucide-react";

import type { GraphEdge, GraphPositions, LiveGraph } from "../../data/types.js";
import type { PresentedGraphNode } from "../../features/graph/presentation/types.js";
import { FlowNode } from "./FlowNode.js";

type GraphFilter = "agents" | "resources" | "all";
type WorkspaceGraphProps = {
  canvasRef: (element: HTMLDivElement | null) => void;
  canvasSize: { width: number; height: number };
  current: LiveGraph;
  edges: GraphEdge[];
  nodes: PresentedGraphNode[];
  edgePaths: Record<string, string>;
  visitedEdges: ReadonlySet<string>;
  activeEdge: string | null;
  activeNode: string | null;
  visitedNodes: ReadonlySet<string>;
  selected: PresentedGraphNode | null;
  draggingNode: string | null;
  nodePositions: GraphPositions;
  expandedAgents: ReadonlySet<string>;
  suppressNodeClickRef: RefObject<boolean>;
  nodeRefs: RefObject<Map<string, HTMLElement>>;
  onResetNodeLayout: () => void;
  onSetSelected: Dispatch<SetStateAction<PresentedGraphNode | null>>;
  onToggleAgentDetails: (id: string) => void;
  onStartNodeDrag: (event: PointerEvent<HTMLButtonElement>, node: PresentedGraphNode) => void;
  onMoveNode: (event: PointerEvent<HTMLButtonElement>) => void;
  onEndNodeDrag: (event: PointerEvent<HTMLButtonElement>) => void;
  filter?: GraphFilter;
  onSetFilter: Dispatch<SetStateAction<GraphFilter>>;
};

export function WorkspaceGraph({
  canvasRef,
  canvasSize,
  current,
  edges,
  nodes,
  edgePaths,
  visitedEdges,
  activeEdge,
  activeNode,
  visitedNodes,
  selected,
  draggingNode,
  nodePositions,
  expandedAgents,
  suppressNodeClickRef,
  nodeRefs,
  onResetNodeLayout,
  onSetSelected,
  onToggleAgentDetails,
  onStartNodeDrag,
  onMoveNode,
  onEndNodeDrag,
  filter = "all",
  onSetFilter,
}: WorkspaceGraphProps) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [panning, setPanning] = useState(false);
  const panRef = useRef<{ startX: number; startY: number; pan: { x: number; y: number } } | null>(null);
  const coreIds = new Set(["input", "prompt", "memory", "supervisor", "model", "output", "checkpoint"]);
  const agentIds = new Set(["researcher", "quant-data-agent", "quant-researcher", "ai-trader-agent"]);
  const resourceIds = new Set(["web-search", "openbb-ingestion", "qlib-worker", "ai-trader-api"]);
  const visibleNodes = nodes.filter((node) => {
    if (filter === "all") return true;
    if (filter === "agents") return coreIds.has(node.id) || agentIds.has(node.id);
    return coreIds.has(node.id) || agentIds.has(node.id) || resourceIds.has(node.id);
  });
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  const visibleEdges = edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to));
  const fitGraph = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    onResetNodeLayout();
  };
  const startPan = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || event.target !== event.currentTarget) return;
    panRef.current = { startX: event.clientX, startY: event.clientY, pan };
    setPanning(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const movePan = (event: PointerEvent<HTMLDivElement>) => {
    if (!panRef.current) return;
    setPan({
      x: panRef.current.pan.x + event.clientX - panRef.current.startX,
      y: panRef.current.pan.y + event.clientY - panRef.current.startY,
    });
  };
  const endPan = (event: PointerEvent<HTMLDivElement>) => {
    if (!panRef.current) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    panRef.current = null;
    setPanning(false);
  };

  return (
    <section className="workspace">
      <div className="canvas-head">
        <div><Activity size={16} /> <strong>Execution graph</strong><b className="graph-mode live">LIVE</b><span>{current.prompt}</span></div>
        <div className="graph-tools">
          <div className="graph-filters" aria-label="Lọc graph">
            {([["agents", "Agents"], ["resources", "Resources"], ["all", "Tất cả"]] as const).map(([value, label]) => (
              <button type="button" className={filter === value ? "active" : ""} onClick={() => onSetFilter?.(value)} key={value}>{label}</button>
            ))}
          </div>
          <div className="zoom-controls" aria-label="Thu phóng graph">
            <button type="button" onClick={() => setZoom((value) => Math.max(.65, Number((value - .15).toFixed(2))))} aria-label="Thu nhỏ graph"><Minus size={12} /></button>
            <span>{Math.round(zoom * 100)}%</span>
            <button type="button" onClick={() => setZoom((value) => Math.min(1.6, Number((value + .15).toFixed(2))))} aria-label="Phóng to graph"><Plus size={12} /></button>
          </div>
          <button className="layout-reset" type="button" onClick={fitGraph} title="Đưa graph vừa khung">
            <Focus size={12} /> Vừa khung
          </button>
          <button className="layout-reset icon-only" type="button" onClick={onResetNodeLayout} title="Đặt lại vị trí node" aria-label="Đặt lại vị trí node">
            <RotateCcw size={12} />
          </button>
        </div>
      </div>
      <div className="canvas graph-viewport">
        <div
          className={`canvas-stage ${panning ? "panning" : ""}`}
          ref={canvasRef}
          data-graph-zoom={zoom}
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
          onPointerDown={startPan}
          onPointerMove={movePan}
          onPointerUp={endPan}
          onPointerCancel={endPan}
        >
        <svg className="edges" viewBox={`0 0 ${canvasSize.width} ${canvasSize.height}`} aria-hidden="true">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
            <filter id="glow"><feGaussianBlur stdDeviation="3.5" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          </defs>
          {visibleEdges.map((edge) => (
            <g key={edge.id}>
              <path className="edge-base" d={edgePaths[edge.id] || ""} markerEnd="url(#arrow)" />
              {(visitedEdges.has(edge.id) || activeEdge === edge.id) && (
                <path className={`edge-live ${activeEdge === edge.id ? "moving" : ""}`} d={edgePaths[edge.id] || ""} markerEnd="url(#arrow)" />
              )}
            </g>
          ))}
        </svg>
        {visibleNodes.map((node) => (
          <FlowNode
            key={node.id}
            node={node}
            position={nodePositions[node.id]}
            active={activeNode === node.id}
            visited={visitedNodes.has(node.id)}
            selected={selected?.id === node.id}
            dragging={draggingNode === node.id}
            onClick={(item) => {
              if (suppressNodeClickRef.current) {
                suppressNodeClickRef.current = false;
                return;
              }
              onSetSelected(item);
              if (item.expandable) onToggleAgentDetails(item.id);
            }}
            expanded={expandedAgents.has(node.id)}
            onPointerDown={onStartNodeDrag}
            onPointerMove={onMoveNode}
            onPointerUp={onEndNodeDrag}
            nodeRef={(element) => {
              if (element) nodeRefs.current.set(node.id, element);
              else nodeRefs.current.delete(node.id);
            }}
          />
        ))}
        <div className="group-label supervisor-label">ORCHESTRATION</div>
        <div className="group-label capability-label">CAPABILITIES & INTEGRATIONS</div>
        </div>
      </div>
    </section>
  );
}
