// @vitest-environment jsdom

import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { Bot } from "lucide-react";
import { afterEach, expect, test, vi } from "vitest";

import { createInitialChatState } from "../chat/model/chatState.js";
import type { ChatController } from "../../hooks/useChat.js";
import { SettingsPage } from "./SettingsPage.js";
import { AgentsPage, ChatPage } from "./pages.js";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function idleChat(): ChatController {
  return {
    ...createInitialChatState(),
    chatEndRef: { current: null },
    progressEndRef: { current: null },
    abortRef: { current: null },
    permissionLease: {
      lease_id: null,
      thread_id: "",
      mode: "safe",
      allow_sensitive: false,
      allowed_tools: [],
      created_at: null,
      expires_at: null,
    },
    permissionBusy: false,
    permissionError: "",
    setPermissionMode: async () => {},
    setChatInput: () => {},
    submitChat: async () => {},
    requestMemoryDeletion: async () => {},
    clearChat: () => {},
    respondToApproval: async () => {},
    stopChat: () => {},
  };
}

function renderSettings(chat: ChatController) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={client}>
      <SettingsPage
        enabledAgents={["supervisor"]}
        skillCount={1}
        chat={chat}
        backendHealth={{ status: "online", refresh: () => {} }}
        topology={{ isPending: false, isError: false, error: null, refresh: () => {} }}
      />
    </QueryClientProvider>,
  );
}

function renderWithRouter(element: ReactNode) {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => element });
  const chatRoute = createRoute({ getParentRoute: () => rootRoute, path: "/chat", component: () => null });
  const router = createRouter({ routeTree: rootRoute.addChildren([indexRoute, chatRoute]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  return render(<RouterProvider router={router} />);
}

function activateTab(tab: HTMLElement) {
  fireEvent.mouseDown(tab, { button: 0, ctrlKey: false });
}

test("Radix inspector tabs expose one selected panel at a time", () => {
  render(
    <ChatPage
      chat={idleChat()}
      current={{ prompt: "", steps: [] }}
      graph={<div>graph canvas</div>}
      nodes={[]}
      selected={null}
      activeNode={null}
      visitedNodes={new Set()}
      expandedAgents={new Set()}
      onToggleAgentDetails={() => {}}
      onCloseNodeDetail={() => {}}
      onSelectNode={() => {}}
    />,
  );

  const traceTab = screen.getByRole("tab", { name: "Dòng chạy" });
  const graphTab = screen.getByRole("tab", { name: "Graph" });
  expect(traceTab.getAttribute("aria-selected")).toBe("true");
  activateTab(graphTab);
  expect(graphTab.getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("heading", { name: "Execution graph" })).not.toBeNull();
});

test("Capabilities exposes tools and resources in a visible detail panel", async () => {
  renderWithRouter(
    <AgentsPage
      rows={[{
        id: "researcher",
        title: "Researcher",
        detail: "Source-backed research.",
        tone: "pink",
        icon: Bot,
        enabled: true,
        skills: ["research"],
        toolLabels: ["web search"],
        resources: ["browser"],
      }]}
      focusedAgentId="researcher"
      topology={{ isPending: false, isError: false, error: null, refresh: () => {} }}
      onInspect={() => {}}
    />,
  );

  expect(await screen.findByRole("heading", { name: "Researcher" })).not.toBeNull();
  expect(screen.getByText("web search")).not.toBeNull();
  expect(screen.getByText("browser")).not.toBeNull();
  expect(screen.queryByRole("button", { name: /Mở rộng/ })).toBeNull();
});

test("Capabilities fails closed when authenticated topology is unavailable", async () => {
  const refresh = vi.fn();
  renderWithRouter(
    <AgentsPage
      rows={[]}
      topology={{ isPending: false, isError: true, error: new Error("Topology offline"), refresh }}
      onInspect={() => {}}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Thử lại" }));
  expect(refresh).toHaveBeenCalledOnce();
  expect(screen.queryByText("Supervisor")).toBeNull();
});

test("Radix graph dialog labels the modal and closes it", () => {
  render(
    <ChatPage
      chat={idleChat()}
      current={{ prompt: "", steps: [] }}
      graph={<div>graph canvas</div>}
      nodes={[]}
      selected={null}
      activeNode={null}
      visitedNodes={new Set()}
      expandedAgents={new Set()}
      onToggleAgentDetails={() => {}}
      onCloseNodeDetail={() => {}}
      onSelectNode={() => {}}
    />,
  );

  activateTab(screen.getByRole("tab", { name: "Graph" }));
  fireEvent.click(screen.getByRole("button", { name: "Mở graph toàn màn hình" }));
  expect(screen.getByRole("dialog", { name: "Execution graph" })).not.toBeNull();
  expect(screen.getByText("graph canvas")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Đóng graph" }));
  expect(screen.queryByRole("dialog", { name: "Execution graph" })).toBeNull();
});

test("Chat exposes an actor-controlled autonomous permission mode", async () => {
  const setPermissionMode = vi.fn(async () => {});
  render(
    <ChatPage
      chat={{ ...idleChat(), setPermissionMode }}
      current={{ prompt: "", steps: [] }}
      graph={<div>graph canvas</div>}
      nodes={[]}
      selected={null}
      activeNode={null}
      visitedNodes={new Set()}
      expandedAgents={new Set()}
      onToggleAgentDetails={() => {}}
      onCloseNodeDetail={() => {}}
      onSelectNode={() => {}}
    />,
  );

  fireEvent.change(screen.getByRole("combobox", { name: "Chế độ permission" }), {
    target: { value: "autonomous" },
  });

  await waitFor(() => expect(setPermissionMode).toHaveBeenCalledWith(
    "autonomous",
    { ttlSeconds: 3600, allowSensitive: false },
  ));
});

test("Full access keeps sensitive effects opt-in and exposes immediate revoke", async () => {
  const setPermissionMode = vi.fn(async () => {});
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(
    <ChatPage
      chat={{
        ...idleChat(),
        permissionLease: {
          lease_id: "azl_v1_" + "a".repeat(32),
          thread_id: "thread-permission",
          mode: "full_access",
          allow_sensitive: false,
          allowed_tools: ["prepare_qlib_dataset", "save_user_memory"],
          created_at: "2026-07-17T00:00:00Z",
          expires_at: "2026-07-17T01:00:00Z",
        },
        setPermissionMode,
      }}
      current={{ prompt: "", steps: [] }}
      graph={<div>graph canvas</div>}
      nodes={[]}
      selected={null}
      activeNode={null}
      visitedNodes={new Set()}
      expandedAgents={new Set()}
      onToggleAgentDetails={() => {}}
      onCloseNodeDetail={() => {}}
      onSelectNode={() => {}}
    />,
  );

  fireEvent.click(screen.getByRole("checkbox", { name: /Tự duyệt cả xóa memory/ }));
  await waitFor(() => expect(setPermissionMode).toHaveBeenCalledWith(
    "full_access",
    { ttlSeconds: 3600, allowSensitive: true },
  ));

  fireEvent.click(screen.getByRole("button", { name: "Tắt quyền tự chủ" }));
  await waitFor(() => expect(setPermissionMode).toHaveBeenCalledWith(
    "safe",
    { ttlSeconds: 3600, allowSensitive: false },
  ));
});

test("Settings requests governed memory deletion without issuing DELETE", async () => {
  const requestMemoryDeletion = vi.fn(async () => {});
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    content: "",
    revision: 0,
    updated_at: null,
  }), { status: 200, headers: { "content-type": "application/json" } }));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderSettings({ ...idleChat(), requestMemoryDeletion });

  const deleteButton = await screen.findByRole("button", { name: "Xóa memory" });
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(deleteButton.hasAttribute("disabled")).toBe(false);
  fireEvent.click(deleteButton);

  expect(requestMemoryDeletion).toHaveBeenCalledOnce();
  expect(fetchMock.mock.calls.some(([, options]) => (
    options && typeof options === "object" && "method" in options && options.method === "DELETE"
  ))).toBe(false);
});

test("Settings exposes approve and reject controls for pending memory deletion", async () => {
  const respondToApproval = vi.fn(async () => {});
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    content: "remembered",
    revision: 3,
    updated_at: "2026-07-17T00:00:00Z",
  }), { status: 200, headers: { "content-type": "application/json" } }));
  renderSettings({
    ...idleChat(),
    pendingApproval: {
      action_requests: [{ name: "delete_user_memory", args: {}, supported: true }],
      status: "verified",
      blocked_reason: null,
    },
    respondToApproval,
  });

  await screen.findByText(/Revision 3/);
  const approval = screen.getByRole("group", { name: "Phê duyệt xóa memory" });
  fireEvent.click(approval.querySelector("button")!);
  await waitFor(() => expect(respondToApproval).toHaveBeenCalledWith("reject"));

  fireEvent.click(screen.getByRole("button", { name: "Phê duyệt xóa" }));
  await waitFor(() => expect(respondToApproval).toHaveBeenCalledWith("approve"));
});
