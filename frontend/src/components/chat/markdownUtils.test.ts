import assert from "node:assert/strict";
import { test } from "vitest";

import { safeHref } from "./markdownUtils.js";

test("safeHref allows ordinary external and in-app links", () => {
  assert.equal(safeHref("https://example.com/research"), "https://example.com/research");
  assert.equal(safeHref("http://example.com/research"), "#");
  assert.equal(safeHref(" https://example.com/research "), "https://example.com/research");
  assert.equal(safeHref("mailto:security@example.com"), "mailto:security@example.com");
  assert.equal(safeHref("/docs/architecture.html"), "/docs/architecture.html");
  assert.equal(safeHref("#section"), "#section");
});

test("safeHref neutralizes scriptable or data URLs from model output", () => {
  assert.equal(safeHref("javascript:alert(1)"), "#");
  assert.equal(safeHref("data:text/html,<script>alert(1)</script>"), "#");
  assert.equal(safeHref("vbscript:msgbox(1)"), "#");
  assert.equal(safeHref("//example.com/implicit-scheme"), "#");
});
