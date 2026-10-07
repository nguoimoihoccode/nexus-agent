import { type FormEventHandler, type RefObject, useEffect, useState } from "react";
import { BrainCircuit, CircleStop, MessageSquare, Send, ShieldCheck, Trash2 } from "lucide-react";

import type {
  ApprovalRequest,
  AuthorizationLease,
  ChatMessage,
  PermissionMode,
} from "../../types/contracts.js";
import type { ProgressItem } from "../../features/chat/model/types.js";
import { MarkdownResponse } from "./MarkdownResponse.js";
import { approvalSummary, canApprove, canReject } from "../../security/approvalContract.js";

export function ChatWidget({
  chatting,
  messages,
  chatError,
  chatInput,
  chatProgress,
  chatElapsed,
  chatEndRef,
  progressEndRef,
  stopChat,
  onClearChat,
  onSubmitChat,
  onSetChatInput,
  pendingApproval,
  onRespondToApproval,
  permissionLease,
  permissionBusy,
  permissionError,
  onSetPermissionMode,
}: {
  chatting: boolean;
  messages: ChatMessage[];
  chatError: string;
  chatInput: string;
  chatProgress: ProgressItem[];
  chatElapsed: number;
  chatEndRef: RefObject<HTMLDivElement | null>;
  progressEndRef: RefObject<HTMLSpanElement | null>;
  stopChat: () => void;
  onClearChat: () => void;
  onSubmitChat: FormEventHandler<HTMLFormElement>;
  onSetChatInput: (value: string) => void;
  pendingApproval: ApprovalRequest | null;
  onRespondToApproval: (decision: "approve" | "reject") => void;
  permissionLease: AuthorizationLease;
  permissionBusy: boolean;
  permissionError: string;
  onSetPermissionMode: (
    mode: PermissionMode,
    options: { ttlSeconds: number; allowSensitive: boolean },
  ) => Promise<void>;
}) {
  return (
    <section className="chat-card chat-card-embedded">
      <div className="card-title">
        <div><MessageSquare size={16} /> Trò chuyện với Supervisor</div>
        <div className="chat-head-actions">
          <button type="button" className="clear-chat" onClick={onClearChat} disabled={!messages.length && !chatError} title="Cuộc trò chuyện mới">
            <Trash2 size={13} />
          </button>
        </div>
      </div>
      <PermissionControls
        lease={permissionLease}
        busy={permissionBusy}
        disabled={chatting || Boolean(pendingApproval)}
        error={permissionError}
        onSetMode={onSetPermissionMode}
      />
      <div className="chat-messages" aria-live="polite">
        {!messages.length && (
          <div className="chat-empty">
            <BrainCircuit size={23} />
            <span>Sẵn sàng nhận yêu cầu mới.</span>
          </div>
        )}
        {messages.map((message, index) => (
          <div className={`chat-message ${message.role}`} key={`${message.role}-${index}`}>
            <span>{message.role === "user" ? "BẠN" : "NEXUS"}</span>
            <div className="chat-bubble">
              {message.role === "assistant" ? (
                <MarkdownResponse content={message.content || (chatting && index === messages.length - 1 ? "Đang suy nghĩ…" : "")} />
              ) : (
                <p>{message.content}</p>
              )}
            </div>
          </div>
        ))}
        <div ref={chatEndRef} />
      </div>
      {chatProgress.length > 0 && (
        <div className={`chat-run-strip ${chatting ? "running" : ""}`} aria-live="polite">
          <i />
          <span>{chatProgress.at(-1)?.label}</span>
          <code>{Math.max(0, ...chatProgress.map((item) => item.toolCallCount || 0))} công cụ · {chatElapsed}s</code>
          <span ref={progressEndRef} />
        </div>
      )}
      {chatError && <div className="chat-error">{chatError}</div>}
      {pendingApproval && (
        <section className="approval-card" aria-live="assertive">
          <strong>Phê duyệt hành động</strong>
          {pendingApproval.blocked_reason && <p className="chat-error">{pendingApproval.blocked_reason}</p>}
          {pendingApproval.action_requests.map((action, index) => (
            <div key={`${action.name}-${index}`}>
              <code>{action.name}</code>
              <dl className="approval-summary">
                {approvalSummary(action).map((item) => (
                  <div key={item.label}>
                    <dt>{item.label}</dt>
                    <dd>{item.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
          <div className="approval-actions">
            {canReject(pendingApproval) ? (
              <button type="button" onClick={() => onRespondToApproval("reject")}>Từ chối</button>
            ) : (
              <button type="button" onClick={onClearChat}>Đặt lại cuộc trò chuyện</button>
            )}
            <button
              type="button"
              className="primary-action"
              disabled={!canApprove(pendingApproval)}
              onClick={() => onRespondToApproval("approve")}
            >Phê duyệt</button>
          </div>
        </section>
      )}
      <form className="chat-form" onSubmit={onSubmitChat}>
        <textarea
          value={chatInput}
          onChange={(event) => onSetChatInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="Nhập yêu cầu cho Nexus…"
          rows={3}
          maxLength={16 * 1024}
          disabled={chatting}
        />
        <button
          type={chatting ? "button" : "submit"}
          disabled={!chatting && !chatInput.trim()}
          onClick={chatting ? stopChat : undefined}
        >
          {chatting ? <CircleStop size={17} /> : <Send size={17} />}
          {chatting ? "Dừng" : "Gửi"}
        </button>
      </form>
    </section>
  );
}

function PermissionControls({
  lease,
  busy,
  disabled,
  error,
  onSetMode,
}: {
  lease: AuthorizationLease;
  busy: boolean;
  disabled: boolean;
  error: string;
  onSetMode: (
    mode: PermissionMode,
    options: { ttlSeconds: number; allowSensitive: boolean },
  ) => Promise<void>;
}) {
  const [ttlSeconds, setTtlSeconds] = useState(3600);

  useEffect(() => {
    if (!lease.created_at || !lease.expires_at) return;
    const duration = new Date(lease.expires_at).getTime() - new Date(lease.created_at).getTime();
    if ([900, 3600, 14_400].includes(Math.round(duration / 1000))) {
      setTtlSeconds(Math.round(duration / 1000));
    }
  }, [lease.created_at, lease.expires_at]);

  const selectMode = (mode: PermissionMode) => {
    if (
      mode === "full_access"
      && lease.mode !== "full_access"
      && !window.confirm(
        "Bật Full access cho thread này? Nexus sẽ tự duyệt mọi hành động được phép, trừ xóa memory và publish nếu bạn chưa bật quyền nhạy cảm.",
      )
    ) return;
    void onSetMode(mode, {
      ttlSeconds,
      allowSensitive: mode === "full_access" && lease.allow_sensitive,
    });
  };

  const setSensitive = (allowSensitive: boolean) => {
    if (
      allowSensitive
      && !window.confirm(
        "Tự duyệt cả xóa memory và publish ra AI-Trader trong thread này? Các hành động vẫn được ghi audit nhưng sẽ không hỏi lại.",
      )
    ) return;
    void onSetMode("full_access", { ttlSeconds, allowSensitive });
  };

  const expiresAt = lease.expires_at
    ? new Date(lease.expires_at).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })
    : null;

  return (
    <section className={`permission-bar mode-${lease.mode}`} aria-label="Permission mode">
      <div className="permission-primary">
        <ShieldCheck size={15} />
        <label>
          <span>Permission</span>
          <select
            aria-label="Chế độ permission"
            value={lease.mode}
            disabled={busy || disabled}
            onChange={(event) => selectMode(event.target.value as PermissionMode)}
          >
            <option value="safe">An toàn</option>
            <option value="autonomous">Tự chủ phiên</option>
            <option value="full_access">Full access</option>
          </select>
        </label>
        {lease.mode !== "safe" && (
          <label>
            <span>Thời hạn</span>
            <select
              aria-label="Thời hạn permission"
              value={ttlSeconds}
              disabled={busy || disabled}
              onChange={(event) => {
                const nextTtl = Number(event.target.value);
                setTtlSeconds(nextTtl);
                void onSetMode(lease.mode, {
                  ttlSeconds: nextTtl,
                  allowSensitive: lease.allow_sensitive,
                });
              }}
            >
              <option value={900}>15 phút</option>
              <option value={3600}>1 giờ</option>
              <option value={14400}>4 giờ</option>
            </select>
          </label>
        )}
        <small>{busy ? "Đang cập nhật…" : expiresAt ? `Hết hạn ${expiresAt}` : "Hỏi trước mỗi effect"}</small>
      </div>
      {lease.mode === "full_access" && (
        <label className="permission-sensitive">
          <input
            type="checkbox"
            checked={lease.allow_sensitive}
            disabled={busy || disabled}
            onChange={(event) => setSensitive(event.target.checked)}
          />
          Tự duyệt cả xóa memory và publish
        </label>
      )}
      {lease.mode !== "safe" && (
        <button
          type="button"
          className="permission-revoke"
          disabled={busy || disabled}
          onClick={() => { void onSetMode("safe", { ttlSeconds, allowSensitive: false }); }}
        >
          Tắt quyền tự chủ
        </button>
      )}
      {error && <p className="permission-error">{error}</p>}
    </section>
  );
}
