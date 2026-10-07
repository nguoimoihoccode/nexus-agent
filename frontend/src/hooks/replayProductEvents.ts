import { fetchRunEvents } from "../api/chat.js";
import { applyReplayPage, createReplayState } from "../features/chat/model/replayState.js";
import type { ChatAction } from "../features/chat/model/types.js";
import type { Dispatch } from "react";

export async function replayRunProductEvents(runId: string | null, dispatch: Dispatch<ChatAction>): Promise<void> {
  if (!runId) return;
  const storageKey = `nexus-run-sequence:${runId}`;
  const after = Number(window.sessionStorage.getItem(storageKey) || 0);
  const page = await fetchRunEvents(runId, after);
  const replay = applyReplayPage(
    { ...createReplayState(runId), lastSequence: after },
    page,
  );
  window.sessionStorage.setItem(storageKey, String(replay.lastSequence));
  dispatch({
    type: "events_replayed",
    runId,
    events: replay.events,
    lastSequence: replay.lastSequence,
    gap: replay.gap,
  });
}
