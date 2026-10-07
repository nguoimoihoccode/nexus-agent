import { Bot, Check, CircleStop, Clock3, LoaderCircle, Play, Wrench, X } from "lucide-react";
import type { PresentedGraphNode } from "../../features/graph/presentation/types.js";
import type { ProgressItem, ProgressStatus } from "../../features/chat/model/types.js";

function StatusIcon({ status }: { status: ProgressStatus }) {
  if (status === "running") return <LoaderCircle className="spin" size={14} />;
  if (status === "waiting") return <Clock3 size={14} />;
  if (status === "failed") return <X size={14} />;
  if (status === "stopped") return <CircleStop size={14} />;
  return <Check size={14} />;
}

export function ExecutionTrace({ chatting, nodes, progress = [], onSelectNode }: {
  chatting: boolean;
  nodes: PresentedGraphNode[];
  progress?: ProgressItem[];
  onSelectNode?: (nodeId: string) => void;
}) {
  const items = progress.map((item, index) => ({
    ...item,
    nodeId: item.agentName && nodes.some((node) => node.id === item.agentName)
      ? item.agentName
      : "supervisor",
    title: item.agentName || "supervisor",
    sequence: index + 1,
  }));
  const completed = items.filter((item) => item.status === "complete").length;

  return (
    <div className="execution-card">
      <div className="card-title">
        <div><Clock3 size={16} /> Dòng thực thi</div>
        <span>{completed} / {items.length} bước</span>
      </div>
      <div className={`progress ${chatting ? "indeterminate" : ""}`}>
        {!chatting && <span style={{ width: `${items.length ? (completed / items.length) * 100 : 0}%` }} />}
      </div>
      <div className="trace-list trace-timeline">
        {!items.length ? (
          <div className="empty-trace"><Play size={20} /> Gửi yêu cầu để bắt đầu một run.</div>
        ) : items.slice(-10).map((item) => (
          <button
            type="button"
            className={`trace-event ${item.status || "complete"}`}
            key={`${item.toolCallId || item.label}-${item.sequence}`}
            onClick={() => onSelectNode?.(item.nodeId)}
            title={`Đi tới ${item.title}`}
          >
            <span className="trace-status"><StatusIcon status={item.status} /></span>
            <span className="trace-copy">
              <strong>{item.toolName ? <Wrench size={12} /> : <Bot size={12} />}{item.title}</strong>
              <span>{item.label}</span>
            </span>
            <time>+{item.at ?? item.sequence - 1}s{Number.isFinite(item.duration) ? ` · ${item.duration}s` : ""}</time>
          </button>
        ))}
      </div>
    </div>
  );
}
