// @vitest-environment jsdom
import assert from "node:assert/strict";
import { beforeEach, test } from "vitest";

import { clearNexusBrowserState } from "./browserState.js";

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

test("clears only Nexus state by default", () => {
  window.localStorage.setItem("nexus-expanded-agents", "[]");
  window.sessionStorage.setItem("nexus-active-run-v1", "{}");
  window.sessionStorage.setItem("oidc.user:test", "token");
  window.localStorage.setItem("unrelated", "keep");

  clearNexusBrowserState();

  assert.equal(window.localStorage.getItem("nexus-expanded-agents"), null);
  assert.equal(window.sessionStorage.getItem("nexus-active-run-v1"), null);
  assert.equal(window.sessionStorage.getItem("oidc.user:test"), "token");
  assert.equal(window.localStorage.getItem("unrelated"), "keep");
});

test("can remove OIDC state after provider sign-out", () => {
  window.sessionStorage.setItem("oidc.user:test", "token");
  clearNexusBrowserState(true);
  assert.equal(window.sessionStorage.getItem("oidc.user:test"), null);
});
