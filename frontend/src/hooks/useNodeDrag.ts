import { type PointerEvent, type RefObject, useEffect, useRef, useState } from "react";

import { defaultPositions, loadNodePositions } from "../data/graphBuilder.js";
import type { GraphPosition, GraphPositions, PositionedGraphNode } from "../data/types.js";

type DragState = {
  id: string;
  startX: number;
  startY: number;
  position: GraphPosition;
  moved: boolean;
};

export function useNodeDrag(
  canvasRef: RefObject<HTMLDivElement | null>,
  nodeRefs: RefObject<Map<string, HTMLElement>>,
  nodes: PositionedGraphNode[],
) {
  const [nodePositions, setNodePositions] = useState<GraphPositions>(() => loadNodePositions(nodes));
  const [draggingNode, setDraggingNode] = useState<string | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const suppressNodeClickRef = useRef(false);

  useEffect(() => setNodePositions(loadNodePositions(nodes)), [nodes]);

  const startNodeDrag = (event: PointerEvent<HTMLButtonElement>, node: PositionedGraphNode) => {
    if (event.button !== 0 || !canvasRef.current) return;
    const position = nodePositions[node.id] || { x: node.x, y: node.y };
    dragRef.current = {
      id: node.id,
      startX: event.clientX,
      startY: event.clientY,
      position,
      moved: false,
    };
    setDraggingNode(node.id);
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const moveNode = (event: PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    const canvas = canvasRef.current;
    if (!drag || !canvas) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    const zoom = Number(canvas.dataset.graphZoom) || 1;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    const element = nodeRefs.current.get(drag.id);
    const maxX = 100 - ((element?.offsetWidth || 143) / canvas.clientWidth) * 95;
    const halfHeight = ((element?.offsetHeight || 58) / canvas.clientHeight) * 50;
    const next = {
      x: Math.min(maxX, Math.max(1, drag.position.x + (dx / zoom / canvas.clientWidth) * 100)),
      y: Math.min(100 - halfHeight, Math.max(halfHeight, drag.position.y + (dy / zoom / canvas.clientHeight) * 100)),
    };
    setNodePositions((items) => ({ ...items, [drag.id]: next }));
  };

  const endNodeDrag = (event: PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    suppressNodeClickRef.current = drag.moved;
    if (drag.moved) window.setTimeout(() => { suppressNodeClickRef.current = false; }, 0);
    dragRef.current = null;
    setDraggingNode(null);
    setNodePositions((items) => {
      window.localStorage.setItem("nexus-node-positions", JSON.stringify(items));
      return items;
    });
  };

  const resetNodeLayout = () => {
    setNodePositions(defaultPositions(nodes));
    window.localStorage.removeItem("nexus-node-positions");
  };

  return {
    nodePositions,
    draggingNode,
    suppressNodeClickRef,
    startNodeDrag,
    moveNode,
    endNodeDrag,
    resetNodeLayout,
  };
}
