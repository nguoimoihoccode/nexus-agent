import assert from "node:assert/strict";
import { test } from "vitest";

import {
  ACTIVE_RUN_STORAGE_KEY,
  clearActiveRun,
  readActiveRun,
  writeActiveRun,
} from "./chat/model/activeRunSession.js";

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test("active run session survives refresh with the last SSE acknowledgement", () => {
  const storage = memoryStorage();
  writeActiveRun("thread-1", "run-1", "event-7", storage);

  assert.deepEqual(readActiveRun(storage), {
    threadId: "thread-1",
    runId: "run-1",
    lastEventId: "event-7",
  });
});

test("active run cleanup cannot erase a newer run", () => {
  const storage = memoryStorage();
  writeActiveRun("thread-1", "run-new", null, storage);

  clearActiveRun("run-old", storage);
  assert.equal(readActiveRun(storage).runId, "run-new");
  clearActiveRun("run-new", storage);
  assert.equal(storage.getItem(ACTIVE_RUN_STORAGE_KEY), null);
});

test("active run session ignores malformed storage", () => {
  const storage = memoryStorage({ [ACTIVE_RUN_STORAGE_KEY]: "not-json" });
  assert.equal(readActiveRun(storage), null);
});
