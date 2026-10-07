import type { ChatMessage } from "../../../types/contracts.js";
import type { LiveStep } from "../../../data/types.js";
import {
  completeApprovalDecision,
  completeProgress,
  recordProgress,
  startApprovalDecision,
} from "./progressState.js";
import type { ChatAction, ChatState, ProgressItem } from "./types.js";

const INITIAL_PROGRESS: ProgressItem = {
  label: "Đang gửi yêu cầu tới supervisor",
  at: 0,
  kind: "status",
  status: "running",
  agentName: "supervisor",
  toolCallCount: 0,
};

const INITIAL_LIVE_STEPS: LiveStep[] = [
  ["input", null, "Nhận câu hỏi từ cửa sổ chat"],
  ["prompt", "input-supervisor", "Nạp supervisor prompt"],
  ["memory", "prompt-supervisor", "Nạp memory và context"],
  ["supervisor", "memory-supervisor", "Bắt đầu xử lý request thật"],
];

export const MAX_LIVE_STEPS = 256;

function appendLiveStep(steps: LiveStep[], step: LiveStep): LiveStep[] {
  const next = [...steps, step];
  if (next.length <= MAX_LIVE_STEPS) return next;
  const pinnedCount = Math.min(INITIAL_LIVE_STEPS.length, steps.length);
  const pinned = steps.slice(0, pinnedCount);
  const tail = next.slice(pinnedCount).slice(-(MAX_LIVE_STEPS - pinnedCount));
  return [...pinned, ...tail];
}

export function createInitialChatState(): ChatState {
  return {
    messages: [],
    chatInput: "",
    chatting: false,
    chatError: "",
    chatProgress: [],
    chatElapsed: 0,
    liveSteps: [],
    livePrompt: "",
    runOutcome: "idle",
    pendingApproval: null,
    productEvents: [],
    productEventRunId: null,
    lastEventSequence: 0,
    replayGap: false,
  };
}

function updateLastAssistant(
  messages: ChatMessage[],
  update: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  return messages.map((message, index) => (
    index === messages.length - 1 && message.role === "assistant"
      ? update(message)
      : message
  ));
}

export { recordProgress } from "./progressState.js";

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "input_changed":
      return { ...state, chatInput: action.value };
    case "tick":
      return state.chatting ? { ...state, chatElapsed: state.chatElapsed + 1 } : state;
    case "run_started":
      return {
        ...state,
        chatInput: "",
        chatting: true,
        chatError: "",
        chatElapsed: 0,
        chatProgress: [{ ...INITIAL_PROGRESS }],
        livePrompt: action.prompt,
        liveSteps: INITIAL_LIVE_STEPS.map((step) => [...step] as LiveStep),
        runOutcome: "running",
        pendingApproval: null,
        messages: [
          ...state.messages,
          { role: "user", content: action.prompt },
          { role: "assistant", content: "" },
        ],
      };
    case "approval_started":
      return {
        ...state,
        chatting: true,
        chatError: "",
        pendingApproval: null,
        runOutcome: "running",
        chatProgress: startApprovalDecision(
          state.chatProgress,
          action.decision,
          action.at ?? state.chatElapsed,
        ),
        messages: [
          ...state.messages,
          {
            role: "user",
            content: action.decision === "approve"
              ? "Đã phê duyệt hành động."
              : "Đã từ chối hành động.",
          },
          { role: "assistant", content: "" },
        ],
      };
    case "thread_restored":
      return {
        ...state,
        messages: action.messages,
        pendingApproval: action.pendingApproval || null,
        runOutcome: action.pendingApproval ? "awaiting_approval" : "idle",
      };
    case "run_reconnecting":
      return {
        ...state,
        chatting: true,
        chatError: "",
        runOutcome: "running",
        chatProgress: [{
          ...INITIAL_PROGRESS,
          label: "Đang nối lại phiên xử lý",
        }],
        messages: state.messages.at(-1)?.role === "assistant"
          ? state.messages
          : [...state.messages, { role: "assistant", content: "" }],
      };
    case "events_replayed":
      {
        const existing = state.productEventRunId === action.runId
          ? state.productEvents
          : [];
        const seen = new Set(existing.map((event) => event.event_id));
        const additions = action.events.filter((event) => !seen.has(event.event_id));
        return {
          ...state,
          productEvents: [...existing, ...additions],
          productEventRunId: action.runId,
          lastEventSequence: action.lastSequence,
          replayGap: action.gap,
        };
      }
    case "token_received":
      return {
        ...state,
        messages: updateLastAssistant(state.messages, (message) => ({
          ...message,
          content: action.replace ? action.token : message.content + action.token,
        })),
      };
    case "progress_recorded": {
      const chatProgress = recordProgress(
        state.chatProgress,
        action.label,
        action.activity,
        action.at,
      );
      if (chatProgress === state.chatProgress) return state;
      return {
        ...state,
        chatProgress,
      };
    }
    case "live_step_added": {
      const previous = state.liveSteps.at(-1);
      if (previous?.[0] === action.step[0] && previous?.[1] === action.step[1]) return state;
      return { ...state, liveSteps: appendLiveStep(state.liveSteps, action.step) };
    }
    case "approval_requested":
      {
        const at = action.at ?? state.chatElapsed;
        return {
          ...state,
          pendingApproval: action.approval,
          runOutcome: "awaiting_approval",
          chatProgress: [...completeProgress(state.chatProgress, at), {
            label: "Đang chờ bạn phê duyệt hành động",
            at,
            status: "waiting",
            kind: "interrupt",
            agentName: action.agentName || "supervisor",
            toolCallCount: Math.max(
              0,
              ...state.chatProgress.map((item) => item.toolCallCount || 0),
            ),
          } satisfies ProgressItem].slice(-12),
        };
      }
    case "approval_message_added":
      return {
        ...state,
        messages: updateLastAssistant(state.messages, (message) => (
          message.content ? message : { ...message, content: "Hành động đang chờ bạn phê duyệt." }
        )),
      };
    case "run_completed":
      return {
        ...state,
        runOutcome: "complete",
        messages: updateLastAssistant(state.messages, (message) => (
          message.content || action.hasAnswer
            ? message
            : { ...message, content: action.emptyMessage || "Agent đã hoàn tất nhưng không trả về câu trả lời." }
        )),
        chatProgress: action.addCompletion === false
          ? completeApprovalDecision(state.chatProgress, action)
          : [
            ...completeProgress(state.chatProgress, action.at),
            {
              label: "Hoàn tất",
              at: action.at,
              status: "complete",
              kind: "status",
              agentName: "supervisor",
              toolCallCount: 0,
            } satisfies ProgressItem,
          ].slice(-12),
      };
    case "run_stopped":
      return {
        ...state,
        runOutcome: "stopped",
        chatProgress: [
          ...completeProgress(state.chatProgress, action.at, "stopped"),
          {
            label: "Đã dừng chờ kết quả",
            at: action.at,
            status: "stopped",
            kind: "status",
            agentName: "supervisor",
            toolCallCount: 0,
          } satisfies ProgressItem,
        ].slice(-12),
        messages: updateLastAssistant(state.messages, (message) => (
          message.content ? message : { ...message, content: "Đã dừng chờ kết quả." }
        )),
      };
    case "run_failed":
      return {
        ...state,
        chatError: action.message,
        runOutcome: "failed",
        messages: state.messages.filter((message, index) => (
          index !== state.messages.length - 1 || message.content
        )),
      };
    case "run_finished":
      return { ...state, chatting: false };
    case "cleared":
      return createInitialChatState();
    case "clear_failed":
      return { ...state, chatError: action.message };
    default:
      action satisfies never;
      return state;
  }
}
