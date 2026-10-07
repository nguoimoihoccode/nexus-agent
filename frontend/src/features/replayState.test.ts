import assert from "node:assert/strict";
import { test } from "vitest";

import { applyReplayPage, createReplayState } from "./chat/model/replayState.js";

const event = (sequence, suffix = String(sequence).padStart(32, "0")) => ({
  event_id: `evt_v1_${suffix}`,
  schema_version: 1,
  sequence,
  actor_key: `v1-${"a".repeat(64)}`,
  run_id: "run-1",
  event_type: "tool.completed",
  occurred_at: "2026-07-14T00:00:00Z",
  sensitivity: "internal",
  payload: {},
});

test("replay reducer deduplicates stable identities", () => {
  const first = applyReplayPage(createReplayState("run-1"), { events: [event(1)] });
  const duplicate = applyReplayPage(first, { events: [event(1)] });
  assert.equal(duplicate.events.length, 1);
  assert.equal(duplicate.lastSequence, 1);
});

test("replay reducer rejects stale malformed and unsupported events", () => {
  const initial = applyReplayPage(createReplayState("run-1"), { events: [event(2)] });
  const next = applyReplayPage(initial, {
    events: [
      event(1),
      { ...event(3), schema_version: 2 },
      { ...event(3), event_id: "bad" },
      { ...event(3), run_id: "other" },
    ],
  });
  assert.equal(next.events.length, 1);
  assert.equal(next.lastSequence, 2);
});

test("replay reducer marks retained and sequence gaps", () => {
  const retained = applyReplayPage(createReplayState("run-1"), {
    events: [event(4)],
    gap: true,
  });
  const sequenceGap = applyReplayPage(retained, { events: [event(6)] });
  assert.equal(sequenceGap.gap, true);
  assert.equal(sequenceGap.lastSequence, 6);
});
