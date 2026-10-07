import { type ReactNode, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Dialog, Tabs } from "radix-ui";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Maximize2,
  MessageSquare,
  Network,
  X,
} from "lucide-react";

import { ChatWidget } from "../../components/chat/ChatWidget";
import { NodeDetailCard } from "../../components/graph/NodeDetailCard";
import { ExecutionTrace } from "../../components/trace/ExecutionTrace";
import { AsyncState } from "../../components/AsyncState.js";
import type { LiveGraph } from "../../data/types.js";
import type { PresentedGraphNode } from "../graph/presentation/types.js";
import type { ChatController } from "../../hooks/useChat.js";

export type AgentRow = {
  id: string;
  title: string;
  detail: string;
  tone: string;
  icon: LucideIcon;
  enabled: boolean;
  skills: string[];
  toolLabels: string[];
  resources: string[];
};

export type TopologyViewState = {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refresh: () => void;
};

export function ChatPage({
  chat,
  current,
  graph,
  nodes,
  selected,
  activeNode,
  visitedNodes,
  expandedAgents,
  onToggleAgentDetails,
  onCloseNodeDetail,
  onSelectNode,
}: {
  chat: ChatController;
  current: LiveGraph;
  graph: ReactNode;
  nodes: PresentedGraphNode[];
  selected: PresentedGraphNode | null;
  activeNode: string | null;
  visitedNodes: ReadonlySet<string>;
  expandedAgents: ReadonlySet<string>;
  onToggleAgentDetails: (id: string) => void;
  onCloseNodeDetail: () => void;
  onSelectNode: (id: string) => void;
}) {
  const [inspectorTab, setInspectorTab] = useState<"trace" | "graph" | "context">("trace");
  const [graphOpen, setGraphOpen] = useState(false);

  return (
    <div className="command-center">
      <div className="chat-workspace live-workspace">
        <section className="chat-column">
          <ChatWidget
            chatting={chat.chatting}
            messages={chat.messages}
            chatError={chat.chatError}
            chatInput={chat.chatInput}
            chatProgress={chat.chatProgress}
            chatElapsed={chat.chatElapsed}
            chatEndRef={chat.chatEndRef}
            progressEndRef={chat.progressEndRef}
            stopChat={chat.stopChat}
            onClearChat={chat.clearChat}
            onSubmitChat={chat.submitChat}
            onSetChatInput={chat.setChatInput}
            pendingApproval={chat.pendingApproval}
            onRespondToApproval={chat.respondToApproval}
            permissionLease={chat.permissionLease}
            permissionBusy={chat.permissionBusy}
            permissionError={chat.permissionError}
            onSetPermissionMode={chat.setPermissionMode}
          />
        </section>

        <Tabs.Root className="run-inspector live-inspector" value={inspectorTab} onValueChange={(value) => {
          if (value === "trace" || value === "graph" || value === "context") setInspectorTab(value);
        }}>
          <Tabs.List className="inspector-tabs" aria-label="Chi tiết run">
            {([["trace", "Dòng chạy"], ["graph", "Graph"], ["context", "Context"]] as const).map(([value, label]) => (
              <Tabs.Trigger
                key={value}
                value={value}
                className={inspectorTab === value ? "active" : ""}
              >
                {label}
              </Tabs.Trigger>
            ))}
          </Tabs.List>
          <Tabs.Content value="trace">
            <ExecutionTrace
              chatting={chat.chatting}
              nodes={nodes}
              progress={chat.chatProgress}
              onSelectNode={onSelectNode}
            />
          </Tabs.Content>
          <Tabs.Content value="graph">
            <section className="graph-preview-card">
              <Network size={26} />
              <h2>Execution graph</h2>
              <p>{current.steps.length} bước đã được ánh xạ vào harness hiện tại.</p>
              <button type="button" className="primary-action" onClick={() => setGraphOpen(true)}>
                <Maximize2 size={15} /> Mở graph toàn màn hình
              </button>
            </section>
          </Tabs.Content>
          <Tabs.Content value="context">
            <>
              <NodeDetailCard
                selected={selected}
                activeNode={activeNode}
                visitedNodes={visitedNodes}
                expandedAgents={expandedAgents}
                onClose={onCloseNodeDetail}
                onToggleAgentDetails={onToggleAgentDetails}
              />
              <div className="run-summary">
                <Metric label="Chế độ" value="Live" />
                <Metric label="Trạng thái" value={chat.chatting ? "Đang chạy" : chat.runOutcome === "idle" ? "Sẵn sàng" : chat.runOutcome} />
                <Metric label="Thời gian" value={`${chat.chatElapsed}s`} />
                <Metric label="Công cụ" value={String(Math.max(0, ...chat.chatProgress.map((item) => item.toolCallCount || 0)))} />
              </div>
            </>
          </Tabs.Content>
        </Tabs.Root>
      </div>

      <Dialog.Root open={graphOpen} onOpenChange={setGraphOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="graph-overlay-backdrop" />
          <Dialog.Content className="graph-overlay">
            <div className="graph-overlay-head">
              <div><Network size={17} /><Dialog.Title>Execution graph</Dialog.Title></div>
              <Dialog.Close asChild><button type="button" aria-label="Đóng graph"><X size={18} /></button></Dialog.Close>
            </div>
            <Dialog.Description className="sr-only">
              Sơ đồ trực quan hóa supervisor, agents, skills và tài nguyên đang thực thi.
            </Dialog.Description>
            {graph}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

export function AgentsPage({ rows, focusedAgentId, topology, onInspect }: {
  rows: AgentRow[];
  focusedAgentId?: string;
  topology: TopologyViewState;
  onInspect: (id: string) => void;
}) {
  if (topology.isPending) {
    return (
      <AsyncState
        kind="loading"
        title="Đang tải capabilities"
        message="Đang đọc topology đã xác thực từ backend."
      />
    );
  }
  if (topology.isError) {
    return (
      <AsyncState
        kind="error"
        title="Không thể tải capabilities"
        message={topology.error instanceof Error
          ? topology.error.message
          : "Topology endpoint không khả dụng."}
        action={(
          <button type="button" className="primary-action" onClick={topology.refresh}>
            Thử lại
          </button>
        )}
      />
    );
  }
  const focused = rows.find((agent) => agent.id === focusedAgentId) ?? null;
  return (
    <div className="capabilities-page">
      <section className="capabilities-intro">
        <div>
          <span>Harness topology</span>
          <h2>Nexus capabilities</h2>
          <p>Khám phá vai trò, skills, tools và resource dependencies của từng agent.</p>
        </div>
        <p>Effectful tools vẫn tuân theo permission mode và approval của thread hiện tại.</p>
      </section>
      <div className="capabilities-layout">
        <div className="agent-grid">
          {rows.map((agent) => {
            const Icon = agent.icon;
            return (
              <article className={`agent-card tone-${agent.tone} ${focusedAgentId === agent.id ? "focused" : ""}`} key={agent.id}>
                <div className="agent-card-head">
                  <span><Icon size={20} /></span>
                  <strong>{agent.title}</strong>
                  <em>{agent.enabled ? "Enabled" : "Disabled"}</em>
                </div>
                <p>{agent.detail}</p>
                <div className="tag-row">
                  <code>{agent.id}</code>
                  {agent.skills.map((skill) => <span key={skill}>{skill}</span>)}
                  {!agent.skills.length && <span>no skills</span>}
                </div>
                <div className="agent-actions">
                  <button type="button" onClick={() => onInspect(agent.id)}>
                    <Activity size={14} /> Xem capability
                  </button>
                </div>
              </article>
            );
          })}
        </div>
        <aside className="capability-detail" aria-live="polite">
          {focused ? (() => {
            const Icon = focused.icon;
            return (
              <>
                <header>
                  <span className={`tone-${focused.tone}`}><Icon size={22} /></span>
                  <div>
                    <small>Capability detail</small>
                    <h2>{focused.title}</h2>
                    <code>{focused.id}</code>
                  </div>
                </header>
                <p>{focused.detail}</p>
                <section>
                  <h3>Skills</h3>
                  <div className="tag-row">
                    {focused.skills.length
                      ? focused.skills.map((skill) => <span key={skill}>{skill}</span>)
                      : <span>Không có project skill</span>}
                  </div>
                </section>
                <section>
                  <h3>Tools</h3>
                  <ul>
                    {focused.toolLabels.length
                      ? focused.toolLabels.map((tool) => <li key={tool}>{tool}</li>)
                      : <li>Không có model-visible tool</li>}
                  </ul>
                </section>
                <section>
                  <h3>Resources</h3>
                  <ul>
                    {focused.resources.length
                      ? focused.resources.map((resource) => <li key={resource}>{resource}</li>)
                      : <li>Không có resource dependency được công bố</li>}
                  </ul>
                </section>
                <Link className="primary-action" to="/chat">
                  <MessageSquare size={14} /> Mở Chat
                </Link>
              </>
            );
          })() : (
            <AsyncState
              title="Chọn một capability"
              message="Mở chi tiết để xem tools, skills và dependencies."
            />
          )}
        </aside>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
