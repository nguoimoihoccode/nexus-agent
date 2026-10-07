import assert from "node:assert/strict";
import { test } from "vitest";

import { parseEventId, parseRuntimeId, runtimePath } from "./runtimeIds.js";

test("runtime identifiers match the backend boundary and are URL encoded", () => {
  assert.equal(parseRuntimeId("run_v1:abc-123"), "run_v1:abc-123");
  assert.equal(runtimePath("run_v1:abc-123"), "run_v1%3Aabc-123");
  assert.equal(parseEventId("evt_v1_abc:12"), "evt_v1_abc:12");
});

test("runtime identifiers reject path and header injection", () => {
  ["../admin", "run/child", "bad\r\nX-Test: injected", "", "x".repeat(201)].forEach((value) => {
    assert.throws(() => parseRuntimeId(value));
  });
  assert.throws(() => parseEventId("event\r\nX-Test: injected"));
});
