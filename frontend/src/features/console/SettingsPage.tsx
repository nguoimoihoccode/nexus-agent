import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  BrainCircuit,
  ClipboardList,
  ServerCog,
  ShieldCheck,
  Wifi,
  WifiOff,
} from "lucide-react";

import { getUserMemory } from "../../api/chat.js";
import { browserSessionEnabled, signOut } from "../../auth/session.js";
import type { BackendHealthStatus } from "../../hooks/useBackendHealth.js";
import type { ChatController } from "../../hooks/useChat.js";
import type { TopologyViewState } from "./pages.js";
import { canApprove } from "../../security/approvalContract.js";

export function SettingsPage({ enabledAgents, skillCount, chat, backendHealth, topology }: {
  enabledAgents: string[];
  skillCount: number;
  chat: ChatController;
  backendHealth: { status: BackendHealthStatus; refresh: () => void };
  topology: TopologyViewState;
}) {
  const [accountError, setAccountError] = useState("");
  const [signingOut, setSigningOut] = useState(false);
  const memory = useQuery({ queryKey: ["user-memory"], queryFn: getUserMemory, retry: false });
  const approvalActions = chat.pendingApproval?.action_requests ?? [];
  const memoryDeletePending = approvalActions.some((action) => (
    action.name === "delete_user_memory"
  ));
  const anotherApprovalPending = Boolean(chat.pendingApproval) && !memoryDeletePending;
  const removeMemory = () => {
    if (!window.confirm("Xóa toàn bộ persistent memory của bạn?")) return;
    void chat.requestMemoryDeletion().then(() => memory.refetch());
  };
  const respondToMemoryApproval = async (decision: "approve" | "reject") => {
    await chat.respondToApproval(decision);
    if (decision === "approve") await memory.refetch();
  };
  const handleSignOut = async () => {
    setSigningOut(true);
    setAccountError("");
    try {
      await signOut();
    } catch (error) {
      setAccountError(error instanceof Error ? error.message : "Không thể đăng xuất phiên hiện tại.");
      setSigningOut(false);
    }
  };
  const memoryValue = memory.data ?? { content: "", revision: 0, updated_at: null };
  const memoryError = memory.error instanceof Error
    ? memory.error.message
    : chat.chatError;
  const permissionLabel = chat.permissionLease.mode === "safe"
    ? "An toàn"
    : chat.permissionLease.mode === "autonomous"
      ? "Tự chủ phiên"
      : "Full access";
  const permissionExpiry = chat.permissionLease.expires_at
    ? new Date(chat.permissionLease.expires_at).toLocaleString("vi-VN")
    : "Hỏi trước mỗi effect";

  return (
    <div className="settings-page">
      <section className="settings-section">
        <header>
          <span>Account & privacy</span>
          <h2>Dữ liệu và tài khoản</h2>
          <p>Quản lý memory do người dùng kiểm soát và phiên đăng nhập hiện tại.</p>
        </header>
        <div className="settings-grid">
          <article className="settings-card memory-settings">
            <BrainCircuit size={20} />
            <div>
              <span>Persistent memory</span>
              <strong>Riêng tư theo tài khoản</strong>
              <p>Memory chỉ được ghi sau khi bạn phê duyệt.</p>
              <p>
                {memory.isPending ? "Đang tải memory…" : (
                  <>
                    Revision {memoryValue.revision}
                    {memoryValue.updated_at
                      ? ` · cập nhật ${new Date(memoryValue.updated_at).toLocaleString("vi-VN")}`
                      : " · chưa có dữ liệu"}
                  </>
                )}
              </p>
              <div className="settings-actions">
                <button
                  type="button"
                  onClick={() => { void memory.refetch(); }}
                  disabled={memory.isFetching}
                >
                  {memory.isFetching ? "Đang tải…" : "Làm mới memory"}
                </button>
                <button
                  type="button"
                  onClick={removeMemory}
                  disabled={memory.isFetching || chat.chatting || Boolean(chat.pendingApproval)}
                >
                  {chat.chatting
                    ? "Đang tạo yêu cầu…"
                    : memoryDeletePending
                      ? "Đang chờ phê duyệt"
                      : anotherApprovalPending
                        ? "Hoàn tất phê duyệt hiện tại"
                        : "Xóa memory"}
                </button>
              </div>
              {memoryDeletePending && (
                <div
                  className="settings-actions memory-approval"
                  role="group"
                  aria-label="Phê duyệt xóa memory"
                >
                  <button
                    type="button"
                    onClick={() => { void respondToMemoryApproval("reject"); }}
                  >Từ chối</button>
                  <button
                    type="button"
                    className="danger-action"
                    disabled={!chat.pendingApproval || !canApprove(chat.pendingApproval)}
                    onClick={() => { void respondToMemoryApproval("approve"); }}
                  >Phê duyệt xóa</button>
                </div>
              )}
              {memoryValue.content && <pre>{memoryValue.content}</pre>}
              {memoryError && <p className="chat-error">{memoryError}</p>}
            </div>
          </article>
          {browserSessionEnabled() && (
            <article className="settings-card">
              <ServerCog size={20} />
              <div>
                <span>Tài khoản</span>
                <strong>OIDC</strong>
                <button type="button" disabled={signingOut} onClick={() => { void handleSignOut(); }}>
                  {signingOut ? "Đang đăng xuất…" : "Đăng xuất"}
                </button>
                {accountError && <p className="chat-error">{accountError}</p>}
              </div>
            </article>
          )}
        </div>
      </section>
      <section className="settings-section">
        <header>
          <span>Current session</span>
          <h2>Permission của thread</h2>
          <p>Permission mode được bind theo thread, không phải thiết lập toàn cục.</p>
        </header>
        <article className="settings-card session-settings">
          <ShieldCheck size={20} />
          <div>
            <span>Permission mode</span>
            <strong>{permissionLabel}</strong>
            <p>{permissionExpiry}</p>
            <p>
              {chat.permissionLease.allow_sensitive
                ? "Sensitive effects được tự duyệt trong lease hiện tại."
                : "Sensitive effects vẫn yêu cầu phê duyệt thủ công."}
            </p>
            <Link className="secondary-action" to="/chat">Quản lý trong Chat</Link>
          </div>
        </article>
      </section>
      <details className="settings-diagnostics">
        <summary>Diagnostics</summary>
        <div className="settings-grid">
          <article className="settings-card">
            {backendHealth.status === "offline" ? <WifiOff size={20} /> : <Wifi size={20} />}
            <div>
              <span>Kết nối backend</span>
              <strong>
                {backendHealth.status === "online"
                  ? "Đã kết nối"
                  : backendHealth.status === "offline"
                    ? "Ngoại tuyến"
                    : "Đang kiểm tra"}
              </strong>
              <button type="button" onClick={backendHealth.refresh}>Kiểm tra lại</button>
            </div>
          </article>
          <article className="settings-card">
            <ServerCog size={20} />
            <div>
              <span>Harness topology</span>
              <strong>
                {topology.isPending
                  ? "Đang tải"
                  : topology.isError
                    ? "Không khả dụng"
                    : `${enabledAgents.length} agents · ${skillCount} skills`}
              </strong>
              {topology.isError && (
                <p>
                  {topology.error instanceof Error
                    ? topology.error.message
                    : "Không thể tải topology."}
                </p>
              )}
              <button type="button" onClick={topology.refresh}>Làm mới topology</button>
            </div>
          </article>
          <article className="settings-card">
            <ClipboardList size={20} />
            <div>
              <span>Trạng thái thread</span>
              <strong>{chat.chatting ? "Đang chạy" : "Sẵn sàng"}</strong>
              <p>{chat.messages.length} tin nhắn trong phiên trình duyệt này.</p>
            </div>
          </article>
          <article className="settings-card">
            <ServerCog size={20} />
            <div>
              <span>Runtime</span>
              <strong>LangGraph API</strong>
              <p>HTTP/SSE · browser-session thread.</p>
            </div>
          </article>
        </div>
      </details>
    </div>
  );
}
