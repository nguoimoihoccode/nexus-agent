export type JsonRecord = Record<string, unknown>;

export type ChatMessage = {
  id?: string | null;
  role: "user" | "assistant";
  content: string;
};

export type { ApprovalAction, ApprovalRequest } from "../security/approvalContract.js";
import type { ApprovalRequest } from "../security/approvalContract.js";

export type StreamActivity = {
  kind: string;
  agentName?: string | null;
  delegatedAgent?: string | null;
  isSubagent?: boolean;
  owner?: string;
  toolName?: string | null;
  skillName?: string | null;
  toolCallId?: string | null;
  modelCallId?: string;
  node?: string;
  namespace?: string[];
  nodes?: string[];
  phase?: string;
  logicalToolCallCount?: number;
  memoryWriteResponse?: boolean;
};

export type StreamCallbacks = {
  onToken?: (token: string, replace: boolean) => void;
  onProgress?: (label: string, activity: StreamActivity) => void;
  onInterrupt?: (approval: ApprovalRequest, activity: StreamActivity) => void;
  onEventId?: (eventId: string) => void;
  onRunStarted?: (runId: string) => void;
};

export type ApprovalDecision = { type: "approve" } | { type: "reject"; message: string };

export type PermissionMode = "safe" | "autonomous" | "full_access";

export type AuthorizationLease = {
  lease_id: string | null;
  thread_id: string;
  mode: PermissionMode;
  allow_sensitive: boolean;
  allowed_tools: string[];
  created_at: string | null;
  expires_at: string | null;
};

export type ProductEvent = {
  event_id: string;
  schema_version: 1;
  sequence: number;
  actor_key: string;
  thread_id?: string | null;
  run_id: string;
  event_type: string;
  occurred_at: string;
  sensitivity: "public" | "internal" | "restricted";
  payload: JsonRecord;
};

export type ReplayPage = {
  events: ProductEvent[];
  last_sequence: number;
  gap: boolean;
  retained_from_sequence?: number | null;
};
