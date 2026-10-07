import type {
  ApprovalRequest,
  ChatMessage,
  ProductEvent,
  StreamActivity,
} from "../../../types/contracts.js";
import type { LiveStep } from "../../../data/types.js";

export type ProgressStatus = "running" | "waiting" | "complete" | "stopped" | "failed";
export type ProgressItem = {
  label: string;
  at: number;
  kind: string;
  status: ProgressStatus;
  agentName: string;
  toolCallCount: number;
  toolName?: string | null;
  toolCallId?: string | null;
  endedAt?: number;
  duration?: number;
};

export type RunOutcome = "idle" | "running" | "awaiting_approval" | "complete" | "stopped" | "failed";
export type ChatState = {
  messages: ChatMessage[];
  chatInput: string;
  chatting: boolean;
  chatError: string;
  chatProgress: ProgressItem[];
  chatElapsed: number;
  liveSteps: LiveStep[];
  livePrompt: string;
  runOutcome: RunOutcome;
  pendingApproval: ApprovalRequest | null;
  productEvents: ProductEvent[];
  productEventRunId: string | null;
  lastEventSequence: number;
  replayGap: boolean;
};

export type CompletionAction = {
  type: "run_completed";
  hasAnswer: boolean;
  at: number;
  emptyMessage?: string;
  addCompletion?: boolean;
};

export type ChatAction =
  | { type: "input_changed"; value: string }
  | { type: "tick" }
  | { type: "run_started"; prompt: string }
  | { type: "approval_started"; decision: "approve" | "reject"; at?: number }
  | { type: "thread_restored"; messages: ChatMessage[]; pendingApproval: ApprovalRequest | null; status?: string | null }
  | { type: "run_reconnecting" }
  | { type: "events_replayed"; runId: string; events: ProductEvent[]; lastSequence: number; gap: boolean }
  | { type: "token_received"; token: string; replace?: boolean }
  | { type: "progress_recorded"; label: string; activity: StreamActivity; at: number }
  | { type: "live_step_added"; step: LiveStep }
  | { type: "approval_requested"; approval: ApprovalRequest; agentName?: string | null | undefined; at?: number }
  | { type: "approval_message_added" }
  | CompletionAction
  | { type: "run_stopped"; at: number }
  | { type: "run_failed"; message: string }
  | { type: "run_finished" }
  | { type: "cleared" }
  | { type: "clear_failed"; message: string };
