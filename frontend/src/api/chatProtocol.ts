import type { ApprovalRequest, JsonRecord, StreamActivity } from "../types/contracts.js";
import { parseApprovalRequest } from "../security/approvalContract.js";
import { parseEventId } from "../security/runtimeIds.js";
import {
  MAX_ASSISTANT_MESSAGE_BYTES,
  MAX_SSE_FRAME_BYTES,
  MAX_SSE_STREAM_BYTES,
  StreamSecurityError,
} from "../security/streamLimits.js";
import {
  SUBAGENT_NAMES,
  SUBAGENT_READING,
  SUBAGENT_REASONING,
  SUBAGENT_TITLE,
  SUBAGENT_WRITING,
  type SubagentName,
} from "./chatProtocolLabels.js";

const MEMORY_TOOLS = ["save_user_memory", "delete_user_memory"];
const APPROVAL_TOOL_AGENT: Record<string, SubagentName> = {
  prepare_qlib_dataset: "quant-data-agent",
  fetch_factor_snapshot: "quant-data-agent",
  run_governed_qlib_experiment: "quant-researcher",
  record_experiment_interpretation: "quant-researcher",
  publish_ai_trader_strategy: "ai-trader-agent",
  publish_ai_trader_discussion: "ai-trader-agent",
};
function asRecord(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function isSubagentName(value: unknown): value is SubagentName {
  return typeof value === "string" && (SUBAGENT_NAMES as readonly string[]).includes(value);
}

export type SseFrame = { eventType: string; eventId: string | null; payload: unknown };

export function parseSseFrame(frame: string): SseFrame | null {
  const lines = frame.split(/\r?\n/);
  const eventType = lines.find((line) => line.startsWith("event:"))?.slice(6).trim() || "";
  const eventId = lines.find((line) => line.startsWith("id:"))?.slice(3).trim() || null;
  const data = lines.filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .join("\n");
  if (!data || data === "[DONE]") return null;
  const payload: unknown = JSON.parse(data);
  return { eventType, eventId, payload };
}

export function describeStreamEvent(eventType: string): { mode: string; namespace: string[] } {
  const [mode = "", ...namespace] = eventType.split("|");
  return { mode, namespace };
}

function subagentFrom(metadata: unknown, namespace: string[], delegatedSubagent: unknown = null): SubagentName | null {
  const record = asRecord(metadata);
  const explicit = record?.lc_agent_name;
  if (isSubagentName(explicit)) return explicit;
  const namespaced = namespace.map((segment) => segment.split(":")[0]).find(isSubagentName);
  if (namespaced) return namespaced;
  const nestedUnderTask = namespace.some((segment) => segment.startsWith("tools:"));
  return nestedUnderTask && isSubagentName(delegatedSubagent) ? delegatedSubagent : null;
}

function parseToolArgs(value: unknown): JsonRecord | null {
  const record = asRecord(value);
  if (record) return record;
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    return asRecord(JSON.parse(value) as unknown);
  } catch {
    return null;
  }
}

export function skillFromToolCall(toolName: unknown, args: unknown): string | null {
  const record = asRecord(args);
  if (typeof toolName !== "string" || !["read_file", "read"].includes(toolName) || !record) return null;
  const path = [record.file_path, record.path, record.file].find((value): value is string => typeof value === "string");
  if (!path) return null;
  return path.replaceAll("\\", "/").match(/(?:^|\/)\.deepagents\/skills\/([^/]+)\/SKILL\.md$/i)?.[1] || null;
}

type ToolCall = { index?: number; id?: string; name?: string; args?: unknown };
type TrackedToolCall = { id: string; name: string; args: JsonRecord | null; delegatedAgent: SubagentName | null };
type ToolEntry = { id: string; name: string; argsText: string; args: JsonRecord | null };

function toolCalls(value: unknown): ToolCall[] {
  return Array.isArray(value)
    ? value.flatMap((call) => {
      const record = asRecord(call);
      if (!record) return [];
      return [{
        ...(typeof record.index === "number" ? { index: record.index } : {}),
        ...(typeof record.id === "string" ? { id: record.id } : {}),
        ...(typeof record.name === "string" ? { name: record.name } : {}),
        ...(record.args !== undefined ? { args: record.args } : {}),
      }];
    })
    : [];
}

export function createToolCallTracker() {
  const entries = new Map<string, ToolEntry>();
  const emitted = new Set<string>();
  return {
    ingest(messageValue: unknown, metadataValue: unknown = {}): TrackedToolCall[] {
      const message = asRecord(messageValue) ?? {};
      const metadata = asRecord(metadataValue) ?? {};
      const completeCalls = toolCalls(message.tool_calls);
      const chunks = toolCalls(message.tool_call_chunks);
      const calls = completeCalls.length ? completeCalls : chunks;
      const ready: TrackedToolCall[] = [];

      calls.forEach((call, position) => {
        const index = call.index ?? position;
        const scope = metadata.langgraph_checkpoint_ns || metadata.checkpoint_ns || metadata.lc_agent_name || "root";
        const key = `${String(scope)}:${String(metadata.langgraph_step ?? "unknown")}:${index}`;
        const entry = entries.get(key) ?? { id: call.id || key, name: "", argsText: "", args: null };
        if (call.id) entry.id = call.id;
        if (call.name) entry.name = call.name;
        if (typeof call.args === "string") entry.argsText += call.args;
        else if (call.args) entry.args = parseToolArgs(call.args);
        entry.args ||= parseToolArgs(entry.argsText);
        entries.set(key, entry);

        const delegatedAgent = entry.name === "task" && isSubagentName(entry.args?.subagent_type)
          ? entry.args.subagent_type
          : null;
        const waitsForArgs = ["task", "read_file", "read"].includes(entry.name);
        const hasIdentity = entry.name && (!waitsForArgs || Boolean(entry.args))
          && (entry.name !== "task" || delegatedAgent !== null);
        if (hasIdentity && !emitted.has(key)) {
          emitted.add(key);
          ready.push({ id: entry.id, name: entry.name, args: entry.args, delegatedAgent });
        }
      });
      return ready;
    },
  };
}

function messagesFromUpdate(payload: unknown): Array<{ node: string; message: JsonRecord }> {
  const record = asRecord(payload);
  if (!record) return [];
  return Object.entries(record).flatMap(([node, update]) => {
    const messages = asRecord(update)?.messages;
    return Array.isArray(messages)
      ? messages.flatMap((message) => {
        const value = asRecord(message);
        return value ? [{ node, message: value }] : [];
      })
      : [];
  });
}

function streamPhase(toolName: string | undefined, reasoning: unknown, content: string): string {
  if (toolName) return "tool_call";
  if (reasoning) return "reasoning";
  if (content) return "content";
  return "streaming";
}

type ConsumeCallbacks = {
  onToken: (token: string, replace: boolean) => void;
  onProgress: (label: string, activity: StreamActivity) => void;
  onInterrupt?: (approval: ApprovalRequest, activity: StreamActivity) => void;
  onEventId?: (eventId: string) => void;
};

export async function consumeChatStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  { onToken, onProgress, onInterrupt, onEventId }: ConsumeCallbacks,
): Promise<boolean> {
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let buffer = "";
  let pendingFrameBytes = 0;
  let totalStreamBytes = 0;
  let assistantMessageBytes = 0;
  let hasAnswer = false;
  const seenToolCallIds = new Set<string>();
  const seenModelActivities = new Set<string>();
  const toolCallTracker = createToolCallTracker();
  let logicalToolCallCount = 0;
  const activeSubagents = new Set<SubagentName>();
  let awaitingMemoryWrite = false;

  while (true) {
    const { value, done } = await reader.read();
    const chunk = value || new Uint8Array();
    totalStreamBytes += chunk.byteLength;
    if (totalStreamBytes > MAX_SSE_STREAM_BYTES) {
      await reader.cancel("SSE stream exceeded the configured size limit.");
      throw new Error("Backend stream vượt quá giới hạn 8 MiB.");
    }
    pendingFrameBytes += chunk.byteLength;
    buffer += decoder.decode(chunk, { stream: !done });
    const events = buffer.split(/\r?\n\r?\n/);
    buffer = events.pop() || "";
    if (events.length > 0) {
      pendingFrameBytes = encoder.encode(buffer).byteLength;
    }
    if (pendingFrameBytes > MAX_SSE_FRAME_BYTES) {
      await reader.cancel("SSE frame exceeded the configured size limit.");
      throw new Error("Backend stream frame vượt quá giới hạn 1 MiB.");
    }
    for (const event of events) {
      if (encoder.encode(event).byteLength > MAX_SSE_FRAME_BYTES) {
        await reader.cancel("SSE frame exceeded the configured size limit.");
        throw new Error("Backend stream frame vượt quá giới hạn 1 MiB.");
      }
      try {
        const parsed = parseSseFrame(event);
        if (!parsed) continue;
        const { eventType, eventId, payload } = parsed;
        if (eventId) {
          try {
            onEventId?.(parseEventId(eventId));
          } catch (error) {
            throw new StreamSecurityError(error instanceof Error ? error.message : "Stream event ID không hợp lệ.");
          }
        }
        const payloadRecord = asRecord(payload);
        if (eventType === "error") {
          const message = payloadRecord?.message || payloadRecord?.error;
          throw new Error(typeof message === "string" ? message : "Backend stream gặp lỗi.");
        }
        const { mode, namespace } = describeStreamEvent(eventType);
        if (mode === "updates") {
          const nestedInterrupts = payloadRecord
            ? Object.values(payloadRecord).flatMap((update) => {
              const interrupts = asRecord(update)?.__interrupt__;
              return Array.isArray(interrupts) ? interrupts : [];
            })
            : [];
          const rawInterrupts = payloadRecord?.__interrupt__ ?? nestedInterrupts;
          const interrupts = Array.isArray(rawInterrupts) ? rawInterrupts : [];
          if (interrupts.length) {
            const first = asRecord(interrupts[0]);
            const rawApproval = asRecord(first?.value) ?? first;
            if (rawApproval) {
              const approval = parseApprovalRequest(rawApproval);
              const action = approval.action_requests[0]?.name;
              onInterrupt?.(approval, {
                kind: "interrupt",
                agentName: subagentFrom(null, namespace)
                  || (typeof action === "string" ? APPROVAL_TOOL_AGENT[action] : null)
                  || "supervisor",
              });
            }
            continue;
          }
          for (const { node, message } of messagesFromUpdate(payload)) {
            const messageType = message.type || message.getType;
            const toolName = typeof message.name === "string" ? message.name : null;
            const agentName = subagentFrom(message.response_metadata, namespace);
            if (messageType === "tool" && toolName === "task") {
              const completedAgent = agentName || [...activeSubagents].at(-1);
              if (completedAgent) {
                activeSubagents.delete(completedAgent);
                onProgress(`${SUBAGENT_TITLE[completedAgent]} trả kết quả cho supervisor`, {
                  kind: "subagent_complete", isSubagent: false, agentName: completedAgent, node,
                });
              }
            } else if (messageType === "tool" && toolName) {
              onProgress(`Công cụ ${toolName} đã hoàn tất`, {
                kind: "tool_result",
                isSubagent: Boolean(agentName),
                agentName,
                owner: agentName || "supervisor",
                toolName,
                toolCallId: typeof message.tool_call_id === "string" ? message.tool_call_id : null,
                node,
              });
            }
          }
          onProgress("Graph step đã hoàn tất", {
            kind: "graph_update", namespace, nodes: payloadRecord ? Object.keys(payloadRecord) : [],
          });
          continue;
        }
        if (mode !== "messages" && mode !== "messages-tuple") continue;
        const message = asRecord(Array.isArray(payload) ? payload[0] : null) ?? {};
        const metadata = asRecord(Array.isArray(payload) ? payload[1] : null) ?? {};
        const type = message.type || message.getType;
        const isAIMessage = type === "AIMessageChunk" || type === "ai";
        const content = typeof message.content === "string" ? message.content : "";
        const trackedToolCalls = toolCallTracker.ingest(message, metadata);
        for (const trackedCall of trackedToolCalls) {
          if (trackedCall.delegatedAgent) activeSubagents.add(trackedCall.delegatedAgent);
        }
        const trackedToolCall = trackedToolCalls[0];
        const delegatedAgent = trackedToolCall?.delegatedAgent || null;
        const sourceAgent = subagentFrom(metadata, namespace, [...activeSubagents].at(-1));
        const agentName = sourceAgent || delegatedAgent;
        const isSubagent = sourceAgent !== null;
        const reasoning = asRecord(message.additional_kwargs)?.reasoning_content;
        const toolName = trackedToolCall?.name;
        const skillName = skillFromToolCall(toolName, trackedToolCall?.args);
        const owner = sourceAgent || "supervisor";
        const phase = streamPhase(toolName, reasoning, content);
        const modelCallId = typeof message.id === "string" && message.id
          ? message.id
          : [metadata.langgraph_checkpoint_ns || namespace.join("|") || "root", metadata.langgraph_step ?? "unknown", owner].map(String).join(":");
        const toolCallId = trackedToolCall?.id || `${String(metadata.langgraph_step ?? "unknown")}:${owner}:${toolName || "unknown"}`;
        const isNewToolCall = Boolean(toolName) && !seenToolCallIds.has(toolCallId);
        if (isNewToolCall) {
          seenToolCallIds.add(toolCallId);
          logicalToolCallCount += 1;
        }
        const activity: StreamActivity = {
          kind: toolName ? "tool_call" : "model_stream",
          isSubagent,
          toolName: toolName ?? null,
          skillName,
          toolCallId,
          logicalToolCallCount,
          agentName,
          delegatedAgent,
          owner,
          phase,
          modelCallId,
          ...(typeof metadata.langgraph_node === "string" ? { node: metadata.langgraph_node } : {}),
          namespace,
        };
        const modelActivityKey = `${modelCallId}:${phase}`;
        const isNewModelActivity = isAIMessage && !toolName && !seenModelActivities.has(modelActivityKey);
        if (isNewModelActivity) seenModelActivities.add(modelActivityKey);
        if (isSubagent && isAIMessage && agentName) activeSubagents.add(agentName);
        if (!isSubagent && isAIMessage && awaitingMemoryWrite) {
          onProgress("Memory xác nhận thao tác", { ...activity, kind: "tool_result", memoryWriteResponse: true });
          awaitingMemoryWrite = false;
        }
        if (toolName && isNewToolCall) {
          if (!isSubagent && MEMORY_TOOLS.includes(toolName)) awaitingMemoryWrite = true;
          onProgress(`Đang gọi công cụ ${toolName} · lượt ${logicalToolCallCount}`, activity);
          for (const extraCall of trackedToolCalls.slice(1)) {
            const extraDelegatedAgent = extraCall.delegatedAgent;
            const extraAgentName = sourceAgent || extraDelegatedAgent;
            const extraToolCallId = extraCall.id || `${String(metadata.langgraph_step ?? "unknown")}:${sourceAgent || "supervisor"}:${extraCall.name}`;
            if (seenToolCallIds.has(extraToolCallId)) continue;
            seenToolCallIds.add(extraToolCallId);
            logicalToolCallCount += 1;
            const extraActivity: StreamActivity = {
              ...activity,
              toolName: extraCall.name,
              skillName: skillFromToolCall(extraCall.name, extraCall.args),
              toolCallId: extraToolCallId,
              logicalToolCallCount,
              agentName: extraAgentName,
              delegatedAgent: extraDelegatedAgent,
              owner: sourceAgent || "supervisor",
            };
            if (!sourceAgent && MEMORY_TOOLS.includes(extraCall.name)) awaitingMemoryWrite = true;
            onProgress(`Đang gọi công cụ ${extraCall.name} · lượt ${logicalToolCallCount}`, extraActivity);
          }
        } else if (isNewModelActivity && sourceAgent && content) {
          onProgress(SUBAGENT_WRITING[sourceAgent], activity);
        } else if (isNewModelActivity && sourceAgent && reasoning) {
          onProgress(SUBAGENT_REASONING[sourceAgent], activity);
        } else if (isNewModelActivity && sourceAgent) {
          onProgress(SUBAGENT_READING[sourceAgent], activity);
        } else if (isNewModelActivity && reasoning) {
          onProgress("Supervisor đang lập kế hoạch", activity);
        } else if (isNewModelActivity && content) {
          onProgress("Supervisor đang soạn câu trả lời", activity);
        }
        if (isSubagent) continue;
        if (isAIMessage && content) {
          assistantMessageBytes += encoder.encode(content).byteLength;
          if (assistantMessageBytes > MAX_ASSISTANT_MESSAGE_BYTES) {
            await reader.cancel("Assistant message exceeded the configured size limit.");
            throw new StreamSecurityError("Câu trả lời của backend vượt quá giới hạn 1 MiB.");
          }
          onToken(content, !hasAnswer);
          hasAnswer = true;
        }
      } catch (error) {
        if (error instanceof StreamSecurityError) throw error;
        if (event.includes("event: error")) throw error;
        // Keep-alive, unknown and malformed non-error frames are forward-compatible.
      }
    }
    if (done) break;
  }
  return hasAnswer;
}
