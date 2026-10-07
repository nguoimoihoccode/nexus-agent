import type { StreamActivity } from "../../../types/contracts.js";
import type { CompletionAction, ProgressItem, ProgressStatus } from "./types.js";

function settleRunning(items: ProgressItem[], at: number, status: ProgressStatus = "complete"): ProgressItem[] {
  return items.map((item) => item.status === "running"
    ? {
      ...item,
      status,
      endedAt: at,
      ...(status === "complete" ? { duration: Math.max(0, at - item.at) } : {}),
    }
    : item);
}

export function completeProgress(items: ProgressItem[], at: number, status: ProgressStatus = "complete"): ProgressItem[] {
  return settleRunning(items, at, status);
}

export function startApprovalDecision(items: ProgressItem[], decision: "approve" | "reject", at: number): ProgressItem[] {
  const settled = completeProgress(items, at);
  const waitingIndex = settled.findLastIndex((item) => item.kind === "interrupt" && item.status === "waiting");
  const waiting = waitingIndex >= 0 ? settled[waitingIndex] : undefined;
  const next: ProgressItem = {
    label: decision === "approve" ? "Đang xử lý hành động đã phê duyệt" : "Đang xử lý quyết định từ chối",
    at,
    status: "running",
    kind: "approval_decision",
    agentName: waiting?.agentName || "supervisor",
    toolCallCount: waiting?.toolCallCount || 0,
  };
  if (waitingIndex < 0) return [...settled, next].slice(-12);
  return settled.map((item, index) => index === waitingIndex ? next : item);
}

export function completeApprovalDecision(items: ProgressItem[], action: CompletionAction): ProgressItem[] {
  const settled = completeProgress(items, action.at);
  const approval = [...settled].reverse().find((item) => (
    item.kind === "approval_decision" || item.kind === "interrupt"
  ));
  return [...settled, {
    label: action.emptyMessage || "Hành động đã được xử lý.",
    at: action.at,
    status: "complete" as const,
    kind: "approval_result",
    agentName: approval?.agentName || "supervisor",
    toolCallCount: approval?.toolCallCount || 0,
  }].slice(-12);
}

export function recordProgress(
  items: ProgressItem[],
  label: string,
  activity: StreamActivity,
  at: number,
): ProgressItem[] {
  const nextStatus: ProgressStatus = activity.kind === "tool_result" || activity.kind === "subagent_complete"
    ? "complete"
    : "running";
  const matchingToolIndex = activity.kind === "tool_result"
    ? items.findIndex((item) => (
      item.status === "running"
      && item.kind === "tool_call"
      && item.toolName === activity.toolName
      && (!activity.toolCallId || !item.toolCallId || item.toolCallId === activity.toolCallId)
    ))
    : -1;
  if (matchingToolIndex >= 0) {
    return items.map((item, index) => index === matchingToolIndex
      ? { ...item, status: "complete", endedAt: at, duration: Math.max(0, at - item.at) }
      : item);
  }
  if (items.at(-1)?.label === label) return items;
  const settled = items.map((item, index) => (
    index === items.length - 1 && item.status === "running"
      ? { ...item, status: "complete" as const, endedAt: at, duration: Math.max(0, at - item.at) }
      : item
  ));
  return [...settled, {
    label,
    at,
    kind: activity.kind || "status",
    status: nextStatus,
    agentName: activity.agentName || activity.owner || "supervisor",
    toolName: activity.toolName || null,
    toolCallId: activity.kind === "tool_call" ? activity.toolCallId || null : null,
    toolCallCount: activity.logicalToolCallCount || 0,
  }].slice(-12);
}
