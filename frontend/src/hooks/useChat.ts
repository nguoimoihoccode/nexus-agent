import {
  type Dispatch,
  type FormEvent,
  type SetStateAction,
  useReducer,
  useRef,
} from "react";

import {
  cancelRun,
  createThread,
  deleteThread,
  resumeChat,
  streamChat,
} from "../api/chat";
import type { ApprovalDecision, StreamActivity } from "../types/contracts.js";
import { canApprove, canReject } from "../security/approvalContract.js";
import type { LiveStep, NexusConfig } from "../data/types.js";
import { chatReducer, createInitialChatState } from "../features/chat/model/chatState.js";
import { projectActivity } from "../features/chat/model/liveGraph.js";
import {
  clearActiveRun,
  readActiveRun,
  writeActiveRun,
} from "../features/chat/model/activeRunSession.js";
import { replayRunProductEvents } from "./replayProductEvents.js";
import { useAuthorizationLease } from "./useAuthorizationLease.js";
import { useChatEffects } from "./useChatEffects.js";

type ChatRunRef = { id: number; inFlight: boolean; controller: AbortController | null };
type ActiveRunCursor = { runId: string | null; lastEventId: string | null };

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Lỗi không xác định.";
}

function isAbortError(error: unknown): error is DOMException {
  return error instanceof Error && error.name === "AbortError";
}

export function useChat(
  nexusConfig: NexusConfig,
  { setExpandedAgents }: { setExpandedAgents: Dispatch<SetStateAction<Set<string>>> },
) {
  const [state, dispatch] = useReducer(chatReducer, undefined, createInitialChatState);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const progressEndRef = useRef<HTMLSpanElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const chatRunRef = useRef<ChatRunRef>({ id: 0, inFlight: false, controller: null });
  const threadRef = useRef<string | null>(window.sessionStorage.getItem("nexus-thread-id"));
  const restoredRun = readActiveRun();
  const activeRunRef = useRef<ActiveRunCursor>(restoredRun
    ? { runId: restoredRun.runId, lastEventId: restoredRun.lastEventId }
    : { runId: null, lastEventId: null });
  const loadedAgentSkillsRef = useRef(new Set<string>());

  const bindThread = (threadId: string) => {
    threadRef.current = threadId;
    window.sessionStorage.setItem("nexus-thread-id", threadId);
  };

  const ensureThread = async () => {
    if (threadRef.current) return threadRef.current;
    const threadId = await createThread();
    bindThread(threadId);
    return threadId;
  };

  const permission = useAuthorizationLease({
    threadRef,
    ensureThread,
    isBlocked: () => chatRunRef.current.inFlight || Boolean(state.pendingApproval),
  });

  const replayProductEvents = (runId: string | null) => replayRunProductEvents(runId, dispatch);

  const addLiveStep = (step: LiveStep) => {
    dispatch({ type: "live_step_added", step });
  };

  const handleActivity = (label: string, activity: StreamActivity, startedAt: number) => {
    const at = Math.floor((Date.now() - startedAt) / 1000);
    dispatch({ type: "progress_recorded", label, activity, at });
    const projection = projectActivity(label, activity, nexusConfig);
    if (
      projection.agentNode
      && projection.initialSkillStep
      && !loadedAgentSkillsRef.current.has(projection.agentNode)
    ) {
      const agentNode = projection.agentNode;
      loadedAgentSkillsRef.current.add(agentNode);
      setExpandedAgents((items) => {
        if (items.has(agentNode)) return items;
        const next = new Set(items);
        next.add(agentNode);
        window.localStorage.setItem("nexus-expanded-agents", JSON.stringify([...next]));
        return next;
      });
      addLiveStep(projection.initialSkillStep);
    }
    projection.steps.forEach(addLiveStep);
  };

  useChatEffects({
    abortRef,
    activeRunRef,
    chatEndRef,
    chatRunRef,
    chatting: state.chatting,
    dispatch,
    handleActivity,
    messageCount: state.messages.length,
    progressCount: state.chatProgress.length,
    progressEndRef,
    replayProductEvents,
    threadRef,
    onThreadMissing: () => permission.resetPermission("", true),
  });

  const runPrompt = async (rawPrompt: string) => {
    const prompt = rawPrompt.trim();
    if (!prompt || chatRunRef.current.inFlight) return;
    const runId = chatRunRef.current.id + 1;
    const controller = new AbortController();
    chatRunRef.current = { id: runId, inFlight: true, controller };
    abortRef.current = controller;
    const isCurrentRun = () => chatRunRef.current.id === runId;
    const startedAt = Date.now();
    dispatch({ type: "run_started", prompt });
    loadedAgentSkillsRef.current.clear();
    let preserveActiveRun = false;

    try {
      const threadId = await ensureThread();
      if (!isCurrentRun()) return;
      let interrupted = false;
      const hasAnswer = await streamChat(
        threadId,
        prompt,
        (token, replace) => {
          if (!isCurrentRun()) return;
          dispatch({ type: "token_received", token, replace });
        },
        (label, activity) => {
          if (!isCurrentRun()) return;
          handleActivity(label, activity, startedAt);
        },
        controller.signal,
        (threadId) => {
          if (!isCurrentRun()) return;
          bindThread(threadId);
          permission.resetPermission(threadId, true);
        },
        (approval, activity) => {
          if (!isCurrentRun()) return;
          interrupted = true;
          dispatch({
            type: "approval_requested",
            approval,
            agentName: activity?.agentName,
            at: Math.floor((Date.now() - startedAt) / 1000),
          });
        },
        null,
        (activeRunId) => {
          activeRunRef.current = { runId: activeRunId, lastEventId: null };
          preserveActiveRun = true;
          writeActiveRun(threadRef.current, activeRunId);
        },
        (lastEventId) => {
          activeRunRef.current.lastEventId = lastEventId;
          writeActiveRun(
            threadRef.current,
            activeRunRef.current.runId,
            lastEventId,
          );
        },
      );
      if (!isCurrentRun()) return;
      if (interrupted) {
        preserveActiveRun = false;
        dispatch({ type: "approval_message_added" });
        return;
      }
      const at = Math.floor((Date.now() - startedAt) / 1000);
      dispatch({ type: "run_completed", hasAnswer, at });
      preserveActiveRun = false;
      replayProductEvents(activeRunRef.current.runId).catch(() => {});
      addLiveStep(["supervisor", "model-supervisor", "Supervisor nhận đầy đủ model response"]);
      addLiveStep(["checkpoint", "supervisor-checkpoint", "Lưu state của cuộc hội thoại"]);
      addLiveStep(["output", "supervisor-output", "Supervisor stream câu trả lời hoàn chỉnh về client"]);
    } catch (error) {
      if (!isCurrentRun()) return;
      if (isAbortError(error)) {
        if (controller.signal.reason === "user_stop") {
          preserveActiveRun = false;
          cancelRun(threadRef.current, activeRunRef.current.runId).catch(() => {});
          dispatch({
            type: "run_stopped",
            at: Math.floor((Date.now() - startedAt) / 1000),
          });
        }
      } else {
        dispatch({
          type: "run_failed",
          message: `Không thể chat với backend: ${errorMessage(error)}`,
        });
      }
    } finally {
      if (isCurrentRun()) {
        chatRunRef.current = { id: runId, inFlight: false, controller: null };
        abortRef.current = null;
        dispatch({ type: "run_finished" });
        if (!preserveActiveRun) {
          clearActiveRun(activeRunRef.current.runId);
          activeRunRef.current = { runId: null, lastEventId: null };
        }
      }
    }
  };

  const submitChat = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await runPrompt(state.chatInput);
  };

  const respondToApproval = async (type: "approve" | "reject") => {
    const approval = state.pendingApproval;
    if (!approval || !threadRef.current || chatRunRef.current.inFlight) return;
    if (type === "approve" && !canApprove(approval)) return;
    if (type === "reject" && !canReject(approval)) return;
    const runId = chatRunRef.current.id + 1;
    const controller = new AbortController();
    chatRunRef.current = { id: runId, inFlight: true, controller };
    abortRef.current = controller;
    const isCurrentRun = () => chatRunRef.current.id === runId;
    const startedAt = Date.now();
    const elapsedAtStart = state.chatElapsed;
    let preserveActiveRun = false;
    dispatch({ type: "approval_started", decision: type, at: elapsedAtStart });

    try {
      let interrupted = false;
      const actionCount = approval.action_requests.length;
      const decision: ApprovalDecision = type === "approve"
        ? { type: "approve" }
        : { type: "reject", message: "Người dùng đã từ chối hành động; không được thử lại." };
      const hasAnswer = await resumeChat(
        threadRef.current,
        Array.from({ length: actionCount }, () => decision),
        {
          onToken: (token, replace) => {
            if (!isCurrentRun()) return;
            dispatch({ type: "token_received", token, replace });
          },
          onProgress: (label, activity) => {
            if (!isCurrentRun()) return;
            handleActivity(label, activity, startedAt - elapsedAtStart * 1000);
          },
          onInterrupt: (nextApproval, activity) => {
            if (!isCurrentRun()) return;
            interrupted = true;
            dispatch({
              type: "approval_requested",
              approval: nextApproval,
              agentName: activity?.agentName,
              at: elapsedAtStart + Math.floor((Date.now() - startedAt) / 1000),
            });
          },
          onRunStarted: (activeRunId) => {
            activeRunRef.current = { runId: activeRunId, lastEventId: null };
            preserveActiveRun = true;
            writeActiveRun(threadRef.current, activeRunId);
          },
          onEventId: (lastEventId) => {
            activeRunRef.current.lastEventId = lastEventId;
            writeActiveRun(
              threadRef.current,
              activeRunRef.current.runId,
              lastEventId,
            );
          },
        },
        controller.signal,
      );
      if (!isCurrentRun()) return;
      if (interrupted) {
        preserveActiveRun = false;
        return;
      }
      preserveActiveRun = false;
      dispatch({
        type: "run_completed",
        hasAnswer,
        emptyMessage: "Hành động đã được xử lý.",
        at: elapsedAtStart + Math.floor((Date.now() - startedAt) / 1000),
        addCompletion: false,
      });
      replayProductEvents(activeRunRef.current.runId).catch(() => {});
    } catch (error) {
      if (!isCurrentRun()) return;
      if (isAbortError(error)) {
        if (controller.signal.reason === "user_stop") {
          preserveActiveRun = false;
          cancelRun(threadRef.current, activeRunRef.current.runId).catch(() => {});
          dispatch({
            type: "run_stopped",
            at: elapsedAtStart + Math.floor((Date.now() - startedAt) / 1000),
          });
        }
      } else {
        dispatch({
          type: "run_failed",
          message: `Không thể xử lý phê duyệt: ${errorMessage(error)}`,
        });
      }
    } finally {
      if (isCurrentRun()) {
        chatRunRef.current = { id: runId, inFlight: false, controller: null };
        abortRef.current = null;
        dispatch({ type: "run_finished" });
        if (!preserveActiveRun) {
          clearActiveRun(activeRunRef.current.runId);
          activeRunRef.current = { runId: null, lastEventId: null };
        }
      }
    }
  };

  const clearChat = () => {
    const threadId = threadRef.current;
    chatRunRef.current.controller?.abort("clear");
    chatRunRef.current = {
      id: chatRunRef.current.id + 1,
      inFlight: false,
      controller: null,
    };
    abortRef.current = null;
    threadRef.current = null;
    window.sessionStorage.removeItem("nexus-thread-id");
    window.localStorage.removeItem("nexus-thread-id");
    clearActiveRun();
    loadedAgentSkillsRef.current.clear();
    permission.resetPermission("", true);
    dispatch({ type: "cleared" });
    deleteThread(threadId).catch((error) => {
      dispatch({
        type: "clear_failed",
        message: `Không thể xóa thread trên backend: ${errorMessage(error)}`,
      });
    });
  };

  return {
    ...state,
    setChatInput: (value: string) => dispatch({ type: "input_changed", value }),
    chatEndRef,
    progressEndRef,
    abortRef,
    permissionLease: permission.permissionLease,
    permissionBusy: permission.permissionBusy,
    permissionError: permission.permissionError,
    setPermissionMode: permission.setPermissionMode,
    submitChat,
    requestMemoryDeletion: () => runPrompt(
      "Hãy xóa toàn bộ persistent memory của tôi bằng delete_user_memory. "
      + "Chỉ đề xuất đúng hành động này và chờ tôi phê duyệt.",
    ),
    clearChat,
    respondToApproval,
    stopChat: () => chatRunRef.current.controller?.abort("user_stop"),
  };
}

export type ChatController = ReturnType<typeof useChat>;
