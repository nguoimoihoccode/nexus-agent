import { z } from "zod";

import { API_URL, ChatApiError, readError, readJson, requestHeaders } from "./chatHttp.js";
import { parseApprovalRequest } from "../security/approvalContract.js";
import { parseRuntimeId, runtimePath } from "../security/runtimeIds.js";
import type {
  ApprovalRequest,
  AuthorizationLease,
  ChatMessage,
  ReplayPage,
} from "../types/contracts.js";

const runtimeIdSchema = z.string().regex(/^[A-Za-z0-9._:-]{1,200}$/);
const threadSchema = z.object({ thread_id: runtimeIdSchema });
const replayPageSchema = z.object({
  events: z.array(z.object({
    event_id: z.string().regex(/^evt_v1_[0-9a-f]{32}$/),
    schema_version: z.literal(1),
    sequence: z.number().int().positive(),
    actor_key: z.string().max(200),
    thread_id: runtimeIdSchema.nullish(),
    run_id: runtimeIdSchema,
    event_type: z.string().max(200),
    occurred_at: z.string().max(100),
    sensitivity: z.enum(["public", "internal", "restricted"]),
    payload: z.record(z.string(), z.unknown()),
  })).max(500),
  last_sequence: z.number().int().nonnegative(),
  gap: z.boolean(),
  retained_from_sequence: z.number().int().nonnegative().nullish(),
});
const memorySchema = z.object({
  content: z.string().max(16 * 1024).default(""),
  revision: z.number().int().nonnegative().default(0),
  updated_at: z.string().nullish(),
});
const authorizationLeaseSchema = z.object({
  lease_id: z.string().regex(/^azl_v1_[0-9a-f]{32}$/).nullable(),
  thread_id: runtimeIdSchema,
  mode: z.enum(["safe", "autonomous", "full_access"]),
  allow_sensitive: z.boolean(),
  allowed_tools: z.array(z.string().max(100)).max(32),
  created_at: z.string().nullish().transform((value) => value ?? null),
  expires_at: z.string().nullish().transform((value) => value ?? null),
});

function contractError(message: string): ChatApiError {
  return new ChatApiError(message, 502);
}

export async function checkBackendHealth(signal?: AbortSignal): Promise<true> {
  const init: RequestInit = {
    method: "GET",
    headers: await requestHeaders({ Accept: "application/json" }),
    cache: "no-store",
  };
  if (signal) init.signal = signal;
  const response = await fetch(`${API_URL}/ok`, init);
  if (!response.ok) throw new ChatApiError(await readError(response), response.status);
  return true;
}

export async function createThread(): Promise<string> {
  const response = await fetch(`${API_URL}/threads`, {
    method: "POST",
    headers: await requestHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ metadata: {} }),
  });
  if (!response.ok) throw new ChatApiError(await readError(response), response.status);
  const parsed = threadSchema.safeParse(await readJson(response));
  if (!parsed.success) throw contractError("Backend returned an invalid thread contract.");
  return parseRuntimeId(parsed.data.thread_id, "Thread ID");
}

function messageText(message: unknown): string {
  if (!message || typeof message !== "object" || !("content" in message)) return "";
  if (typeof message.content === "string") return message.content;
  if (!Array.isArray(message.content)) return "";
  return message.content.map((item) => {
    if (typeof item === "string") return item;
    if (item && typeof item === "object" && "text" in item && typeof item.text === "string") return item.text;
    return "";
  }).join("");
}

const MAX_RESTORED_MESSAGES = 500;
const MAX_RESTORED_MESSAGE_BYTES = 1024 * 1024;
const encoder = new TextEncoder();

export async function getThreadState(threadId: string): Promise<{
  messages: ChatMessage[];
  pendingApproval: ApprovalRequest | null;
  status: string | null;
}> {
  const response = await fetch(`${API_URL}/threads/${runtimePath(threadId, "Thread ID")}/state`, {
    headers: await requestHeaders({ Accept: "application/json" }),
    cache: "no-store",
  });
  if (!response.ok) throw new ChatApiError(await readError(response), response.status);
  const raw = await readJson(response);
  const state = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const values = state.values && typeof state.values === "object" ? state.values as Record<string, unknown> : {};
  const rawMessages = Array.isArray(values.messages) ? values.messages.slice(-MAX_RESTORED_MESSAGES) : [];
  let restoredBytes = 0;
  const messages = rawMessages.flatMap<ChatMessage>((message) => {
    if (!message || typeof message !== "object") return [];
    const record = message as Record<string, unknown>;
    const type = String(record.type || record.role || "").toLowerCase();
    const role = ["human", "user"].includes(type)
      ? "user" as const
      : ["ai", "assistant", "aimessage"].includes(type)
        ? "assistant" as const
        : null;
    const content = messageText(record);
    const bytes = encoder.encode(content).byteLength;
    if (!role || !content || bytes > MAX_RESTORED_MESSAGE_BYTES || restoredBytes + bytes > MAX_RESTORED_MESSAGE_BYTES) return [];
    restoredBytes += bytes;
    return [{ id: typeof record.id === "string" && record.id.length <= 200 ? record.id : null, role, content }];
  });
  const interruptsValue = state.interrupts;
  const interrupts = Array.isArray(interruptsValue)
    ? interruptsValue
    : interruptsValue && typeof interruptsValue === "object"
      ? Object.values(interruptsValue)
      : [];
  const first = interrupts[0];
  const rawApproval = first && typeof first === "object" && "value" in first ? first.value : first;
  const pendingApproval = rawApproval && typeof rawApproval === "object"
    ? parseApprovalRequest(rawApproval)
    : null;
  const metadata = state.metadata && typeof state.metadata === "object" ? state.metadata as Record<string, unknown> : {};
  return { messages, pendingApproval, status: typeof metadata.status === "string" ? metadata.status : null };
}

export async function fetchRunEvents(runId: string, after = 0): Promise<ReplayPage> {
  if (!Number.isInteger(after) || after < 0) throw new ChatApiError("Replay cursor không hợp lệ.", 422);
  const query = new URLSearchParams({ after: String(after) });
  const response = await fetch(`${API_URL}/v1/runs/${runtimePath(runId, "Run ID")}/events?${query}`, {
    headers: await requestHeaders({ Accept: "application/json" }),
    cache: "no-store",
  });
  if (!response.ok) throw new ChatApiError(await readError(response), response.status);
  const parsed = replayPageSchema.safeParse(await readJson(response));
  if (!parsed.success) throw contractError("Backend returned an invalid replay contract.");
  return parsed.data as ReplayPage;
}

export async function deleteThread(threadId: string | null): Promise<void> {
  if (!threadId) return;
  const response = await fetch(`${API_URL}/threads/${runtimePath(threadId, "Thread ID")}`, {
    method: "DELETE",
    headers: await requestHeaders(),
  });
  if (!response.ok && response.status !== 404) throw new ChatApiError(await readError(response), response.status);
}

export async function getUserMemory(): Promise<z.infer<typeof memorySchema>> {
  const response = await fetch(`${API_URL}/v1/memory`, {
    headers: await requestHeaders({ Accept: "application/json" }),
    cache: "no-store",
  });
  if (!response.ok) throw new ChatApiError(await readError(response), response.status);
  const parsed = memorySchema.safeParse(await readJson(response));
  if (!parsed.success) throw contractError("Backend returned an invalid memory contract.");
  return parsed.data;
}

export async function getAuthorizationLease(threadId: string): Promise<AuthorizationLease> {
  const query = new URLSearchParams({ thread_id: parseRuntimeId(threadId, "Thread ID") });
  const response = await fetch(`${API_URL}/v1/authorization-leases/current?${query}`, {
    headers: await requestHeaders({ Accept: "application/json" }),
    cache: "no-store",
  });
  if (!response.ok) throw new ChatApiError(await readError(response), response.status);
  const parsed = authorizationLeaseSchema.safeParse(await readJson(response));
  if (!parsed.success) throw contractError("Backend returned an invalid authorization lease contract.");
  return parsed.data;
}

export async function createAuthorizationLease({
  threadId,
  mode,
  ttlSeconds,
  allowSensitive,
}: {
  threadId: string;
  mode: "autonomous" | "full_access";
  ttlSeconds: number;
  allowSensitive: boolean;
}): Promise<AuthorizationLease> {
  const response = await fetch(`${API_URL}/v1/authorization-leases`, {
    method: "POST",
    headers: await requestHeaders({
      Accept: "application/json",
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({
      thread_id: parseRuntimeId(threadId, "Thread ID"),
      mode,
      ttl_seconds: ttlSeconds,
      allow_sensitive: allowSensitive,
    }),
  });
  if (!response.ok) throw new ChatApiError(await readError(response), response.status);
  const parsed = authorizationLeaseSchema.safeParse(await readJson(response));
  if (!parsed.success) throw contractError("Backend returned an invalid authorization lease contract.");
  return parsed.data;
}

export async function revokeAuthorizationLease(leaseId: string): Promise<void> {
  const response = await fetch(`${API_URL}/v1/authorization-leases/${runtimePath(leaseId, "Lease ID")}`, {
    method: "DELETE",
    headers: await requestHeaders(),
  });
  if (!response.ok && response.status !== 404) {
    throw new ChatApiError(await readError(response), response.status);
  }
}
