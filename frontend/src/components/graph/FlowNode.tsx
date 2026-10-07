import type { PointerEvent, PointerEventHandler } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { GraphPosition } from "../../data/types.js";
import type { PresentedGraphNode } from "../../features/graph/presentation/types.js";

type FlowNodeProps = {
  node: PresentedGraphNode;
  position: GraphPosition | undefined;
  active: boolean;
  visited: boolean;
  selected: boolean;
  dragging: boolean;
  expanded: boolean;
  onClick: (node: PresentedGraphNode) => void;
  onPointerDown: (event: PointerEvent<HTMLButtonElement>, node: PresentedGraphNode) => void;
  onPointerMove: PointerEventHandler<HTMLButtonElement>;
  onPointerUp: PointerEventHandler<HTMLButtonElement>;
  nodeRef: (element: HTMLButtonElement | null) => void;
};

export function FlowNode({
  node,
  position,
  active,
  visited,
  selected,
  dragging,
  expanded,
  onClick,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  nodeRef,
}: FlowNodeProps) {
  const Icon = node.icon;
  const safePosition = position || { x: node.x, y: node.y };
  return (
    <button
      ref={nodeRef}
      className={`flow-node tone-${node.tone} ${node.large ? "large" : ""} ${active ? "active" : ""} ${visited ? "visited" : ""} ${selected ? "selected" : ""} ${dragging ? "dragging" : ""}`}
      style={{ left: `${safePosition.x}%`, top: `${safePosition.y}%` }}
      onClick={() => onClick(node)}
      onPointerDown={(event) => onPointerDown(event, node)}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      type="button"
      aria-pressed={selected}
      aria-label={`${node.title}: ${node.subtitle}`}
    >
      <span className="node-icon"><Icon size={node.large ? 25 : 20} strokeWidth={1.8} /></span>
      <span className="node-copy">
        <strong>{node.title}</strong>
        <small>{node.subtitle}</small>
      </span>
      {active && <span className="node-pulse" />}
      {node.expandable && (
        <span
          className={`node-expander ${expanded ? "open" : ""}`}
          title={expanded ? "Thu gọn chi tiết" : node.detailSummary}
        >
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <em>{expanded ? "OPEN" : node.detailSummary}</em>
        </span>
      )}
    </button>
  );
}
