// @vitest-environment jsdom
import { cleanup } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetModules();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

test("loads an opaque session and attaches only the CSRF header", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    authenticated: true,
    auth_mode: "browser",
    session_key: "public-session-key",
    csrf_token: "c".repeat(43),
  }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const { authenticatedHeaders, loadSession } = await import("./session.js");

  const session = await loadSession();
  const headers = await authenticatedHeaders({ Accept: "application/json" });

  expect(session?.session_key).toBe("public-session-key");
  expect(headers.get("X-Nexus-CSRF")).toBe("c".repeat(43));
  expect(headers.has("Authorization")).toBe(false);
  expect(fetchMock).toHaveBeenCalledWith("/auth/session", expect.objectContaining({
    credentials: "same-origin",
    cache: "no-store",
  }));
  expect(window.localStorage.length).toBe(0);
  expect(window.sessionStorage.length).toBe(0);
});

test("fails closed on malformed session contracts", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    authenticated: true,
    access_token: "must-not-be-accepted", // pragma: allowlist secret
  }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const { loadSession } = await import("./session.js");

  await expect(loadSession()).rejects.toThrow(/browser session không hợp lệ/);
});

test("treats 401 as an unauthenticated browser", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 401 }));
  const { loadSession } = await import("./session.js");

  await expect(loadSession()).resolves.toBeNull();
});

test("failed server logout still clears sensitive local session state", async () => {
  vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({
      authenticated: true,
      auth_mode: "browser",
      session_key: "public-session-key",
      csrf_token: "c".repeat(43),
    }), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(null, { status: 500 }));
  const { browserSessionEnabled, loadSession, signOut } = await import("./session.js");
  await loadSession();
  window.sessionStorage.setItem("nexus-active-run-v1", "sensitive-state");
  window.sessionStorage.setItem("oidc.user:legacy", "legacy-token");
  window.localStorage.setItem("unrelated", "keep");

  await expect(signOut()).rejects.toThrow(/Không thể đăng xuất/);

  expect(browserSessionEnabled()).toBe(false);
  expect(window.sessionStorage.getItem("nexus-active-run-v1")).toBeNull();
  expect(window.sessionStorage.getItem("oidc.user:legacy")).toBeNull();
  expect(window.localStorage.getItem("unrelated")).toBe("keep");
});
