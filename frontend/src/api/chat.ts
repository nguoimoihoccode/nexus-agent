import { consumeChatStream } from "./chatProtocol.js";
import { API_URL, ChatApiError, readError, requestHeaders } from "./chatHttp.js";
import { createThread } from "./chatResources.js";
import { parseEventId, parseRuntimeId, runtimePath } from "../security/runtimeIds.js";
import type {
  ApprovalDecision,
  ApprovalRequest,
  JsonRecord,
  StreamActivity,
  StreamCallbacks,
} from "../types/contracts.js";

export { createToolCallTracker, describeStreamEvent, parseSseFrame, skillFromToolCall } from "./chatProtocol.js";
export { ChatApiError } from "./chatHttp.js";
export {
  checkBackendHealth,
  createAuthorizationLease,
  createThread,
  deleteThread,
  fetchRunEvents,
  getThreadState,
  getAuthorizationLease,
  getUserMemory,
  revokeAuthorizationLease,
} from "./chatResources.js";
export type {
  ApprovalRequest,
  AuthorizationLease,
  ChatMessage,
  PermissionMode,
  ProductEvent,
  ReplayPage,
  StreamActivity,
} from "../types/contracts.js";

type TokenCallback = (token: string, replace: boolean) => void;
type ProgressCallback = (label: string, activity: StreamActivity) => void;
type InterruptCallback = (approval: ApprovalRequest, activity: StreamActivity) => void;
const MAX_PROMPT_BYTES = 16 * 1024;

export function isMissingThreadError(error: unknown): boolean {
  return error instanceof ChatApiError
    && error.status === 404
    && /thread or assistant not found/i.test(error.message);
}

export async function streamChat(
  threadId: string,
  prompt: string,
  onToken: TokenCallback = () => {},
  onProgress: ProgressCallback = () => {},
  signal?: AbortSignal,
  onThreadReset?: (threadId: string) => void,
  onInterrupt?: InterruptCallback,
  command: JsonRecord | null = null,
  onRunStarted?: (runId: string) => void,
  onEventId?: (eventId: string) => void,
): Promise<boolean> {
  if (!command && new TextEncoder().encode(prompt).byteLength > MAX_PROMPT_BYTES) {
    throw new ChatApiError("Nội dung nhập vượt quá giới hạn 16 KiB.", 413);
  }
  let activeThreadId = parseRuntimeId(threadId, "Thread ID");
  let response: Response | undefined;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const init: RequestInit = {
      method: "POST",
      headers: await requestHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        assistant_id: "supervisor",
        ...(command ? { command } : { input: { messages: [{ role: "user", content: prompt }] } }),
        stream_mode: ["messages-tuple", "updates"],
        stream_subgraphs: true,
        on_disconnect: "continue",
      }),
    };
    if (signal) init.signal = signal;
    response = await fetch(`${API_URL}/threads/${runtimePath(activeThreadId, "Thread ID")}/runs/stream`, init);
    if (response.ok) break;
    const error = new ChatApiError(await readError(response), response.status);
    if (attempt > 0 || !isMissingThreadError(error)) throw error;
    activeThreadId = await createThread();
    onThreadReset?.(activeThreadId);
  }
  if (!response?.body) throw new Error("Trình duyệt không hỗ trợ streaming response.");
  const rawRunId = response.headers.get("content-location")?.split("/").at(-1) || null;
  const runId = rawRunId ? parseRuntimeId(rawRunId, "Run ID") : null;
  if (runId) onRunStarted?.(runId);
  let lastEventId: string | null = null;
  let hasAnswer = false;
  let activeResponse = response;
  for (let reconnect = 0; reconnect < 3; reconnect += 1) {
    if (!activeResponse.body) throw new Error("Backend stream không có response body.");
    try {
      const currentHasAnswer = await consumeChatStream(activeResponse.body.getReader(), {
        onToken: (token, replace) => {
          onToken(token, replace && !hasAnswer);
          hasAnswer = true;
        },
        onProgress,
        ...(onInterrupt ? { onInterrupt } : {}),
        onEventId: (eventId) => {
          lastEventId = eventId;
          onEventId?.(eventId);
        },
      });
      return hasAnswer || currentHasAnswer;
    } catch (error) {
      if (signal?.aborted || !runId || reconnect === 2) throw error;
      activeResponse = await joinRunStream(activeThreadId, runId, lastEventId, signal);
    }
  }
  return hasAnswer;
}

export async function joinRunStream(
  threadId: string,
  runId: string,
  lastEventId: string | null = null,
  signal?: AbortSignal,
): Promise<Response> {
  const modes = encodeURIComponent(JSON.stringify(["messages-tuple", "updates"]));
  const headers = new Headers(await requestHeaders({ Accept: "text/event-stream" }));
  if (lastEventId) headers.set("Last-Event-ID", parseEventId(lastEventId));
  const init: RequestInit = { headers };
  if (signal) init.signal = signal;
  const response = await fetch(`${API_URL}/threads/${runtimePath(threadId, "Thread ID")}/runs/${runtimePath(runId, "Run ID")}/stream?stream_mode=${modes}`, init);
  if (!response.ok) throw new ChatApiError(await readError(response), response.status);
  return response;
}

export async function reconnectRun(
  threadId: string,
  runId: string,
  lastEventId: string | null,
  callbacks: StreamCallbacks = {},
  signal?: AbortSignal,
): Promise<boolean> {
  let activeResponse = await joinRunStream(threadId, runId, lastEventId, signal);
  let acknowledgedEventId = lastEventId;
  let hasAnswer = false;
  for (let reconnect = 0; reconnect < 3; reconnect += 1) {
    if (!activeResponse.body) throw new Error("Backend stream không có response body.");
    try {
      const currentHasAnswer = await consumeChatStream(activeResponse.body.getReader(), {
        onToken: (token, replace) => {
          callbacks.onToken?.(token, replace && !hasAnswer);
          hasAnswer = true;
        },
        onProgress: callbacks.onProgress || (() => {}),
        ...(callbacks.onInterrupt ? { onInterrupt: callbacks.onInterrupt } : {}),
        onEventId: (eventId) => {
          acknowledgedEventId = eventId;
          callbacks.onEventId?.(eventId);
        },
      });
      return hasAnswer || currentHasAnswer;
    } catch (error) {
      if (signal?.aborted || reconnect === 2) throw error;
      activeResponse = await joinRunStream(threadId, runId, acknowledgedEventId, signal);
    }
  }
  return hasAnswer;
}

export async function resumeChat(
  threadId: string,
  decisions: ApprovalDecision[],
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<boolean> {
  return streamChat(
    threadId,
    "",
    callbacks.onToken,
    callbacks.onProgress,
    signal,
    undefined,
    callbacks.onInterrupt,
    { resume: { decisions } },
    callbacks.onRunStarted,
    callbacks.onEventId,
  );
}

export async function cancelRun(threadId: string | null, runId: string | null): Promise<void> {
  if (!threadId || !runId) return;
  const response = await fetch(`${API_URL}/threads/${runtimePath(threadId, "Thread ID")}/runs/${runtimePath(runId, "Run ID")}/cancel`, {
    method: "POST",
    headers: await requestHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ action: "interrupt" }),
  });
  if (!response.ok && response.status !== 404 && response.status !== 409) {
    throw new ChatApiError(await readError(response), response.status);
  }
}
