import { type Dispatch, type RefObject, useEffect } from "react";

import { ChatApiError, getThreadState, reconnectRun } from "../api/chat.js";
import type { StreamActivity } from "../types/contracts.js";
import type { ChatAction } from "../features/chat/model/types.js";
import {
  clearActiveRun,
  readActiveRun,
  writeActiveRun,
} from "../features/chat/model/activeRunSession.js";

type ChatRunRef = { id: number; inFlight: boolean; controller: AbortController | null };
type ActiveRunCursor = { runId: string | null; lastEventId: string | null };
type ChatEffectsOptions = {
  abortRef: RefObject<AbortController | null>;
  activeRunRef: RefObject<ActiveRunCursor>;
  chatEndRef: RefObject<HTMLDivElement | null>;
  chatRunRef: RefObject<ChatRunRef>;
  chatting: boolean;
  dispatch: Dispatch<ChatAction>;
  handleActivity: (label: string, activity: StreamActivity, startedAt: number) => void;
  messageCount: number;
  progressCount: number;
  progressEndRef: RefObject<HTMLSpanElement | null>;
  replayProductEvents: (runId: string | null) => Promise<void>;
  threadRef: RefObject<string | null>;
  onThreadMissing: () => void;
};

export function useChatEffects({
  abortRef,
  activeRunRef,
  chatEndRef,
  chatRunRef,
  chatting,
  dispatch,
  handleActivity,
  messageCount,
  progressCount,
  progressEndRef,
  replayProductEvents,
  threadRef,
  onThreadMissing,
}: ChatEffectsOptions) {
  useEffect(() => {
    if (messageCount) {
      chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [chatEndRef, messageCount]);

  useEffect(() => {
    if (progressCount) progressEndRef.current?.scrollIntoView({ block: "nearest" });
  }, [progressCount, progressEndRef]);

  useEffect(() => () => abortRef.current?.abort("unmount"), [abortRef]);

  useEffect(() => {
    if (!chatting) return undefined;
    const timer = window.setInterval(() => dispatch({ type: "tick" }), 1000);
    return () => window.clearInterval(timer);
  }, [chatting, dispatch]);

  useEffect(() => {
    const threadId = threadRef.current;
    if (!threadId) return undefined;
    let disposed = false;
    let reconnectController: AbortController | null = null;
    getThreadState(threadId)
      .then(async (restored) => {
        if (disposed || chatRunRef.current.inFlight) return;
        dispatch({ type: "thread_restored", ...restored });
        const active = readActiveRun();
        if (!active || active.threadId !== threadId || restored.pendingApproval) {
          if (active && (active.threadId !== threadId || restored.pendingApproval)) {
            clearActiveRun(active.runId);
            activeRunRef.current = { runId: null, lastEventId: null };
          }
          return;
        }
        const localRunId = chatRunRef.current.id + 1;
        reconnectController = new AbortController();
        chatRunRef.current = {
          id: localRunId,
          inFlight: true,
          controller: reconnectController,
        };
        abortRef.current = reconnectController;
        activeRunRef.current = active;
        dispatch({ type: "run_reconnecting" });
        let interrupted = false;
        try {
          await reconnectRun(
            threadId,
            active.runId,
            active.lastEventId,
            {
              onToken: (token, replace) => {
                if (!disposed) dispatch({ type: "token_received", token, replace });
              },
              onProgress: (label, activity) => {
                if (!disposed) handleActivity(label, activity, Date.now());
              },
              onInterrupt: (approval, activity) => {
                interrupted = true;
                if (!disposed) dispatch({
                  type: "approval_requested",
                  approval,
                  agentName: activity?.agentName,
                  at: 0,
                });
              },
              onEventId: (eventId) => {
                activeRunRef.current.lastEventId = eventId;
                writeActiveRun(threadId, active.runId, eventId);
              },
            },
            reconnectController.signal,
          );
          if (!disposed) {
            const finalState = await getThreadState(threadId);
            dispatch({ type: "thread_restored", ...finalState });
            if (!interrupted && !finalState.pendingApproval) {
              dispatch({ type: "run_completed", hasAnswer: true, at: 0 });
            }
          }
          clearActiveRun(active.runId);
          replayProductEvents(active.runId).catch(() => {});
        } catch (error) {
          if (
            !disposed
            && (!(error instanceof Error) || error.name !== "AbortError")
            && !(error instanceof ChatApiError && [404, 409].includes(error.status))
          ) {
            dispatch({
              type: "run_failed",
              message: `Không thể nối lại phiên xử lý: ${error instanceof Error ? error.message : "Lỗi không xác định."}`,
            });
          }
          if (error instanceof ChatApiError && [404, 409].includes(error.status)) {
            clearActiveRun(active.runId);
            const finalState = await getThreadState(threadId).catch(() => null);
            if (!disposed && finalState) dispatch({ type: "thread_restored", ...finalState });
          }
        } finally {
          if (chatRunRef.current.id === localRunId) {
            chatRunRef.current = {
              id: localRunId,
              inFlight: false,
              controller: null,
            };
            abortRef.current = null;
            if (!disposed) dispatch({ type: "run_finished" });
          }
        }
      })
      .catch((error) => {
        if (error instanceof ChatApiError && error.status === 404) {
          threadRef.current = null;
          window.sessionStorage.removeItem("nexus-thread-id");
          onThreadMissing();
        }
      });
    return () => {
      disposed = true;
      reconnectController?.abort("unmount");
    };
  }, []);
}
