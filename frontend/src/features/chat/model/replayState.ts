import type { ProductEvent, ReplayPage } from "../../../types/contracts.js";

const EVENT_ID = /^evt_v1_[0-9a-f]{32}$/;

export type ReplayState = {
  runId: string | null;
  lastSequence: number;
  events: ProductEvent[];
  seenEventIds: Set<string>;
  gap: boolean;
};

export function createReplayState(runId: string | null = null): ReplayState {
  return {
    runId,
    lastSequence: 0,
    events: [],
    seenEventIds: new Set(),
    gap: false,
  };
}

export function applyReplayPage(state: ReplayState, page: ReplayPage): ReplayState {
  const next: ReplayState = {
    ...state,
    events: [...state.events],
    seenEventIds: new Set(state.seenEventIds),
    gap: state.gap || Boolean(page?.gap),
  };
  for (const event of page?.events || []) {
    if (
      event?.schema_version !== 1
      || !EVENT_ID.test(event?.event_id || "")
      || event?.run_id !== state.runId
      || !Number.isInteger(event?.sequence)
      || event.sequence <= 0
      || next.seenEventIds.has(event.event_id)
      || event.sequence <= next.lastSequence
    ) continue;
    if (next.lastSequence > 0 && event.sequence > next.lastSequence + 1) next.gap = true;
    next.events.push(event);
    next.seenEventIds.add(event.event_id);
    next.lastSequence = event.sequence;
  }
  return next;
}
