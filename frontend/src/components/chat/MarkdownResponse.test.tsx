// @vitest-environment jsdom
import assert from "node:assert/strict";
import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { afterEach, test } from "vitest";

import { MarkdownResponse } from "./MarkdownResponse.js";

afterEach(cleanup);

function renderMarkdown(content: string) {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <MarkdownResponse content={content} />,
  });
  const evidenceRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/evidence/$experimentId",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, evidenceRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(<RouterProvider router={router} />);
}

test("links valid plain and inline-code experiment IDs to the dossier", async () => {
  const plain = `exp_v1_${"a".repeat(32)}`;
  const code = `exp_v1_${"b".repeat(32)}`;
  renderMarkdown(`Kết quả ${plain} và \`${code}\`.`);

  assert.equal((await screen.findByRole("link", { name: plain })).getAttribute("href"), `/evidence/${plain}`);
  assert.equal(screen.getByRole("link", { name: code }).getAttribute("href"), `/evidence/${code}`);
});

test("does not link malformed experiment identifiers", async () => {
  const malformed = `exp_v1_${"a".repeat(32)}z`;
  renderMarkdown(`exp_v1_not-valid ${malformed}`);

  assert.ok(await screen.findByText(`exp_v1_not-valid ${malformed}`));
  assert.equal(screen.queryByRole("link"), null);
});
