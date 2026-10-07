import { ChevronDown, ChevronRight, X } from "lucide-react";
import type { PresentedGraphNode } from "../../features/graph/presentation/types.js";

export function NodeDetailCard({
  selected,
  activeNode,
  visitedNodes,
  expandedAgents,
  onClose,
  onToggleAgentDetails,
}: {
  selected: PresentedGraphNode | null;
  activeNode: string | null;
  visitedNodes: ReadonlySet<string>;
  expandedAgents: ReadonlySet<string>;
  onClose: () => void;
  onToggleAgentDetails: (id: string) => void;
}) {
  const DetailIcon = selected?.icon ?? X;

  return (
    <aside className="detail-card">
      <button className="close-detail" type="button" onClick={onClose} aria-label="Đóng chi tiết node"><X size={15} /></button>
      {selected ? (
        <>
          <span className={`detail-icon tone-${selected.tone}`}><DetailIcon size={24} /></span>
          <div className="detail-kicker">NODE ĐANG CHỌN</div>
          <h2>{selected.title}</h2>
          <code>{selected.subtitle}</code>
          <p>{selected.detail}</p>
          {selected.expandable && (
            <button
              className="detail-toggle"
              type="button"
              onClick={() => onToggleAgentDetails(selected.id)}
            >
              {expandedAgents.has(selected.id) ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              {expandedAgents.has(selected.id) ? "Thu gọn chi tiết" : `Mở ${selected.detailSummary}`}
            </button>
          )}
          <div className="node-state"><span className={activeNode === selected.id ? "running" : visitedNodes.has(selected.id) ? "complete" : ""} />
            {activeNode === selected.id ? "Đang thực thi" : visitedNodes.has(selected.id) ? "Đã hoàn tất" : "Sẵn sàng"}
          </div>
        </>
      ) : <div className="empty-detail">Chọn một node để xem vai trò của nó.</div>}
    </aside>
  );
}
