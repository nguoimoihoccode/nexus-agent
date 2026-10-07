import { readFileSync } from "node:fs";

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";


const CSRF_TOKEN = "c".repeat(43);
const EXPERIMENT_A = `exp_v1_${"a".repeat(32)}`;
const EXPERIMENT_B = `exp_v1_${"b".repeat(32)}`;
const ARTIFACT_ID = `art_v1_${"c".repeat(24)}`;
const THREAD_ID = "thread-browser-e2e";
const RUN_ID = "run-browser-e2e";
const PAGE_ERRORS = new WeakMap();
const DOSSIER_FIXTURE = JSON.parse(readFileSync(
  new URL("../src/features/dossier/fixtures/dossier-v1.json", import.meta.url),
  "utf8",
));

test.beforeEach(async ({ page }) => {
  const errors = [];
  PAGE_ERRORS.set(page, errors);
  page.on("pageerror", (error) => errors.push(error.message));
});

test.afterEach(async ({ page }) => {
  expect(PAGE_ERRORS.get(page)).toEqual([]);
});

async function installRuntime(page, authenticated = true) {
  await page.route("**/auth/session", (route) => route.fulfill(authenticated ? {
    json: {
      authenticated: true,
      auth_mode: "browser",
      session_key: "browser-e2e-session",
      csrf_token: CSRF_TOKEN,
    },
  } : { status: 401, json: { detail: "Browser session is expired or revoked." } }));
  await page.route("**/auth/logout", (route) => route.fulfill({ json: { logout_url: null } }));
}

function stream(events) {
  return `${events.map(({ id, event, data }) => [
    `id: ${id}`,
    `event: ${event}`,
    `data: ${JSON.stringify(data)}`,
  ].join("\n")).join("\n\n")}\n\n`;
}

function chatTopology() {
  return {
    schema_version: "1",
    agents: { supervisor: { enabled: true, skills: [], title: "Supervisor", description: "Coordinates Nexus capabilities.", tool_labels: ["save user memory", "delete user memory"] } },
    skills: {},
    resource_edges: [{ from: "supervisor", to: "checkpoint", kind: "uses" }],
  };
}

function experimentCatalog() {
  const item = (experimentId, status, model) => ({
    experiment_id: experimentId,
    dataset_revision_id: `dsr_v1_${experimentId === EXPERIMENT_A ? "a" : "b"}`,
    status,
    progress_stage: status === "completed" ? "finished" : null,
    created_at: experimentId === EXPERIMENT_A ? "2026-07-20T08:00:00+00:00" : "2026-07-19T08:00:00+00:00",
    updated_at: experimentId === EXPERIMENT_A ? "2026-07-20T08:10:00+00:00" : "2026-07-19T08:10:00+00:00",
    specification_summary: {
      universe: "csi300",
      model,
      feature_set: "Alpha158",
      strategy_type: "topk_dropout",
      test_start: "2025-01-01",
      test_end: "2025-03-31",
    },
  });
  return {
    schema_version: "1",
    items: [item(EXPERIMENT_A, "completed", "lightgbm"), item(EXPERIMENT_B, "completed", "linear")],
    next_cursor: null,
  };
}

function threadState(messages, pendingApproval = null) {
  return {
    values: { messages },
    interrupts: pendingApproval ? [{ value: pendingApproval }] : [],
    metadata: { status: pendingApproval ? "interrupted" : "running" },
  };
}

async function fulfillChatFoundation(route, url) {
  if (url.pathname === "/api/ok") {
    await route.fulfill({ json: { ok: true } });
    return true;
  }
  if (url.pathname === "/api/v1/topology") {
    await route.fulfill({ json: chatTopology() });
    return true;
  }
  if (url.pathname.startsWith("/api/v1/runs/") && url.pathname.endsWith("/events")) {
    await route.fulfill({ json: { events: [], gap: false } });
    return true;
  }
  return false;
}

function dossier({ large = false } = {}) {
  const result = {
    ...structuredClone(DOSSIER_FIXTURE),
    artifacts: [{
      artifact_id: ARTIFACT_ID,
      artifact_type: "metrics",
      content_hash: "sha256:browser-e2e",
      storage_status: "ready",
      download_eligible: true,
    }],
    metrics: [{
      metric_id: "metric-browser-e2e",
      metric_group: "portfolio",
      name: "annualized_return",
      value: 0.17,
      unit: "ratio",
      meaning: "Annualized portfolio return",
      calculation_version: "1",
      evidence_status: "complete",
      missing_evidence: [],
    }],
    publications: [{
      publication_id: "pub-browser-e2e",
      publication_type: "research_report",
      status: "published",
      approval: {
        status: "approved",
        execution_status: "completed",
        approval_request_id: "approval-browser-e2e",
      },
    }],
  };

  if (!large) return result;

  return {
    ...result,
    workflow: {
      workflow_id: "workflow-browser-scale",
      state: "completed",
      stage: "completed",
      thread_id: "thread-browser-scale",
      run_id: "run-browser-scale",
      normalized_input: { universe: "production-scale-fixture" },
      transitions: Array.from({ length: 28 }, (_, index) => ({
        sequence: index + 1,
        from_state: index === 0 ? "queued" : "running",
        to_state: index === 27 ? "completed" : "running",
        stage: `stage-${String(index + 1).padStart(2, "0")}`,
      })),
    },
    sources: Array.from({ length: 35 }, (_, index) => ({
      source_record_id: `source-browser-${String(index + 1).padStart(2, "0")}`,
      locator: `https://evidence.nexus.test/source-${index + 1}`,
      retrieval_status: "retrieved",
      content_hash: `sha256:source-${String(index + 1).padStart(2, "0")}`,
      metadata: { title: `Research source ${String(index + 1).padStart(2, "0")}` },
    })),
    artifacts: Array.from({ length: 26 }, (_, index) => ({
      artifact_id: `art_v1_${(index + 1).toString(16).padStart(24, "0")}`,
      artifact_type: `evidence-${String(index + 1).padStart(2, "0")}`,
      content_hash: `sha256:artifact-${String(index + 1).padStart(2, "0")}`,
      storage_status: "ready",
      download_eligible: true,
    })),
  };
}

async function installProductApi(page, {
  catalogStatus = 200,
  dossierStatus = 200,
  dossierDelayMs = 0,
  largeDossier = false,
  largeLineage = false,
} = {}) {
  const requests = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    requests.push({
      method: request.method(),
      path: `${url.pathname}${url.search}`,
      authorization: request.headers().authorization || "",
      csrf: request.headers()["x-nexus-csrf"] || "",
    });

    if (url.pathname === "/api/ok") {
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === "/api/v1/topology") {
      return route.fulfill({ json: chatTopology() });
    }
    if (url.pathname === "/api/v1/memory") {
      return route.fulfill({ json: { content: "", revision: 0, updated_at: null } });
    }
    if (url.pathname === "/api/v1/experiments") {
      if (catalogStatus !== 200) {
        return route.fulfill({ status: catalogStatus, json: { detail: "Quant experiment access denied." } });
      }
      return route.fulfill({ json: experimentCatalog() });
    }
    if (url.pathname === `/api/v1/experiments/${EXPERIMENT_A}/dossier`) {
      if (dossierDelayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, dossierDelayMs));
      }
      if (dossierStatus !== 200) {
        return route.fulfill({
          status: dossierStatus,
          json: { detail: "Cross-tenant evidence access denied." },
        });
      }
      return route.fulfill({ json: dossier({ large: largeDossier }) });
    }
    if (url.pathname === "/api/v1/experiments/compare") {
      return route.fulfill({ json: {
        schema_version: "1",
        compatible: true,
        configurations: [
          { experiment_id: EXPERIMENT_A, model: "lightgbm" },
          { experiment_id: EXPERIMENT_B, model: "lightgbm" },
        ],
        metrics: [{
          metric_group: "portfolio",
          name: "annualized_return",
          meaning: "Annualized portfolio return",
          values: { [EXPERIMENT_A]: 0.17, [EXPERIMENT_B]: 0.15 },
        }],
        incompatibilities: [],
      } });
    }
    if (url.pathname === `/api/v1/lineage/${EXPERIMENT_A}`) {
      return route.fulfill({ json: {
        nodes: largeLineage ? [
          { node_id: EXPERIMENT_A, node_type: "experiment" },
          ...Array.from({ length: 32 }, (_, index) => ({
            node_id: `lineage-node-${String(index + 1).padStart(2, "0")}`,
            node_type: index % 2 === 0 ? "artifact" : "metric",
          })),
        ] : [{ node_id: EXPERIMENT_A, node_type: "experiment" }],
        edges: [],
      } });
    }
    if (url.pathname === `/api/v1/experiments/${EXPERIMENT_A}/export`) {
      return route.fulfill({
        body: JSON.stringify({ schema_version: "1", experiment_id: EXPERIMENT_A }),
        contentType: "application/octet-stream",
        headers: { "content-disposition": `attachment; filename="${EXPERIMENT_A}-dossier-v1.json"` },
      });
    }
    if (url.pathname === `/api/v1/artifacts/${ARTIFACT_ID}/content`) {
      return route.fulfill({
        body: "browser-e2e-artifact",
        contentType: "application/octet-stream",
        headers: { "content-disposition": "attachment; filename=\"metrics.json\"" },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found in browser fixture." } });
  });
  return requests;
}

async function openEvidence(page) {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await page.getByRole("link", { name: "Experiments" }).click();
  await expect(page.getByRole("heading", { name: "Experiment history" })).toBeVisible();
}

test("opaque browser session supplies CSRF without exposing bearer tokens", async ({ page }) => {
  await installRuntime(page);
  const requests = await installProductApi(page);

  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await expect.poll(() => requests.filter((item) => item.path === "/api/v1/topology").length).toBe(1);

  expect(requests.filter((item) => item.path.startsWith("/api/")).every(
    (item) => item.authorization === "" && item.csrf === CSRF_TOKEN,
  )).toBe(true);
  const browserStorage = await page.evaluate(() => ({
    local: Object.fromEntries(Object.entries(window.localStorage)),
    session: Object.fromEntries(Object.entries(window.sessionStorage)),
  }));
  expect(JSON.stringify(browserStorage)).not.toMatch(/access_token|refresh_token|id_token|Bearer/);
});

test("authenticated Experiments journey covers catalog, dossier, lineage, comparison and export", async ({ page }) => {
  await installRuntime(page);
  const requests = await installProductApi(page);
  await openEvidence(page);

  await page.getByRole("checkbox", { name: `Chọn experiment ${EXPERIMENT_A}` }).check();
  await page.getByRole("checkbox", { name: `Chọn experiment ${EXPERIMENT_B}` }).check();
  await page.getByRole("button", { name: "So sánh" }).click();
  await expect(page.getByText("Comparable evidence", { exact: true })).toBeVisible();
  await page.goBack();
  await page.getByRole("link", { name: EXPERIMENT_A }).first().click();
  await expect(page).toHaveURL(new RegExp(`/evidence/${EXPERIMENT_A}$`));
  await expect(page.getByRole("heading", { name: EXPERIMENT_A })).toBeVisible();
  await expect(page.getByText("partial evidence", { exact: true })).toBeVisible();
  await expect(page.getByText("annualized_return", { exact: true })).toBeVisible();
  await expect(page.getByText("research_report · published", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Load lineage" }).click();
  await expect(page.getByText("experiment", { exact: true })).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export bundle" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(`${EXPERIMENT_A}-dossier-v1.json`);

  await page.getByRole("tab", { name: "Comparison" }).click();
  await page.getByLabel("2–5 experiment IDs").fill(`${EXPERIMENT_A}, ${EXPERIMENT_B}`);
  await page.getByRole("button", { name: "Compare" }).click();
  await expect(page).toHaveURL(/\/evidence\/compare/);
  await expect(page.getByText("Comparable evidence", { exact: true })).toBeVisible();
  await expect(page.getByText(EXPERIMENT_B, { exact: true })).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/evidence/${EXPERIMENT_A}$`));
  await expect(page.getByRole("heading", { name: EXPERIMENT_A })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: EXPERIMENT_A })).toBeVisible();
  await page.goForward();
  await expect(page.getByText("Comparable evidence", { exact: true })).toBeVisible();

  expect(requests.filter((item) => item.path.includes("/api/v1/")).every(
    (item) => item.authorization === "" && item.csrf === CSRF_TOKEN,
  )).toBe(true);
});

test("Experiments validates direct IDs inline without navigating to Not Found", async ({ page }) => {
  await installRuntime(page);
  await installProductApi(page);
  await openEvidence(page);

  await page.getByLabel("Open by experiment ID").fill("exp_v1_invalid");
  await page.getByRole("button", { name: "Mở" }).click();
  await expect(page.getByRole("alert")).toContainText("32 ký tự hex");
  await expect(page).toHaveURL(/\/evidence\/?(?:\?status=all)?$/);
});

test("Experiments catalog renders a typed unauthorized state", async ({ page }) => {
  await installRuntime(page);
  await installProductApi(page, { catalogStatus: 403 });

  await page.goto("/evidence");
  await expect(page.getByText("Không có quyền truy cập", { exact: true })).toBeVisible();
  await expect(page.getByText("Quant experiment access denied.", { exact: true })).toBeVisible();
});

test("capability deep link survives reload and exposes tools and resources", async ({ page }) => {
  await installRuntime(page);
  await installProductApi(page);

  await page.goto("/agents/supervisor");
  await expect(page.getByRole("heading", { name: "Capabilities", exact: true })).toBeVisible();
  await expect(page.locator(".agent-card.focused")).toContainText("Supervisor");
  await expect(page.getByRole("heading", { name: "Supervisor" })).toBeVisible();
  await expect(page.getByText("save user memory", { exact: true })).toBeVisible();
  await expect(page.getByText("checkpoint", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator(".agent-card.focused")).toContainText("Supervisor");
  await expect(page.getByRole("link", { name: "Capabilities" })).toHaveAttribute("aria-current", "page");
});

test("authenticated product shell has no serious WCAG violations", async ({ page }) => {
  await installRuntime(page);
  await installProductApi(page);

  const violations = [];
  for (const path of ["/", "/chat", "/settings", "/agents", "/evidence"]) {
    await page.goto(path);
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    violations.push(...result.violations
      .filter((violation) => ["serious", "critical"].includes(violation.impact || ""))
      .map((violation) => ({
        path,
        id: violation.id,
        impact: violation.impact,
        targets: violation.nodes.flatMap((node) => node.target),
      })));
  }

  expect(violations).toEqual([]);
});

test("Experiments renders a typed unauthorized dossier state for cross-tenant denial", async ({ page }) => {
  await installRuntime(page);
  const requests = await installProductApi(page, { dossierStatus: 403 });
  await openEvidence(page);

  await page.getByRole("link", { name: EXPERIMENT_A }).first().click();
  await expect(
    page.getByRole("status").getByText("Không có quyền truy cập", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Cross-tenant evidence access denied.", { exact: true })).toBeVisible();
  expect(requests.find((item) => item.path.endsWith("/dossier"))?.authorization)
    .toBe("");
});

test("Experiments remains accessible and paginated for slow large dossiers on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installRuntime(page);
  await installProductApi(page, {
    dossierDelayMs: 350,
    largeDossier: true,
    largeLineage: true,
  });
  await openEvidence(page);

  await page.getByRole("link", { name: EXPERIMENT_A }).first().click();
  await expect(page.getByRole("status").getByText("Đang tải dossier", { exact: true }))
    .toBeVisible();
  await expect(page.getByRole("heading", { name: EXPERIMENT_A })).toBeVisible();

  const sourcesPager = page.getByRole("navigation", { name: "Sources pagination" });
  await expect(sourcesPager.getByText("1–10 of 35", { exact: false })).toBeVisible();
  await expect(page.getByText("Research source 01", { exact: true })).toBeVisible();
  const nextSources = page.getByRole("button", { name: "Next Sources page" });
  await nextSources.focus();
  await expect(nextSources).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(sourcesPager.getByText("11–20 of 35", { exact: false })).toBeVisible();
  await expect(page.getByText("Research source 11", { exact: true })).toBeVisible();
  await expect(page.getByText("Research source 01", { exact: true })).toHaveCount(0);
  await nextSources.click();
  await nextSources.click();
  await expect(sourcesPager.getByText("31–35 of 35", { exact: false })).toBeVisible();
  await expect(page.getByText("Research source 35", { exact: true })).toBeVisible();
  await expect(nextSources).toBeDisabled();

  const workflowPager = page.getByRole("navigation", { name: "Workflow events pagination" });
  const nextWorkflow = workflowPager.getByRole("button", { name: "Next Workflow events page" });
  await nextWorkflow.click();
  await expect(workflowPager.getByText("11–20 of 28", { exact: false })).toBeVisible();
  await nextWorkflow.click();
  await expect(workflowPager.getByText("21–28 of 28", { exact: false })).toBeVisible();
  await expect(nextWorkflow).toBeDisabled();

  const artifactsPager = page.getByRole("navigation", { name: "Artifacts pagination" });
  const nextArtifacts = artifactsPager.getByRole("button", { name: "Next Artifacts page" });
  await nextArtifacts.click();
  await expect(artifactsPager.getByText("11–20 of 26", { exact: false })).toBeVisible();
  await expect(page.getByText("evidence-11", { exact: true })).toBeVisible();
  await nextArtifacts.click();
  await expect(artifactsPager.getByText("21–26 of 26", { exact: false })).toBeVisible();
  await expect(page.getByText("evidence-26", { exact: true })).toBeVisible();
  await expect(nextArtifacts).toBeDisabled();

  await page.getByRole("button", { name: "Load lineage" }).click();
  const lineagePager = page.getByRole("navigation", { name: "Lineage nodes pagination" });
  await expect(lineagePager.getByText("1–10 of 33", { exact: false })).toBeVisible();
  const nextLineage = lineagePager.getByRole("button", { name: "Next Lineage nodes page" });
  await nextLineage.click();
  await expect(lineagePager.getByText("11–20 of 33", { exact: false })).toBeVisible();
  await expect(page.getByText("lineage-node-10", { exact: true })).toBeVisible();
  await nextLineage.click();
  await nextLineage.click();
  await expect(lineagePager.getByText("31–33 of 33", { exact: false })).toBeVisible();
  await expect(page.getByText("lineage-node-32", { exact: true })).toBeVisible();
  await expect(nextLineage).toBeDisabled();

  const overflow = await page.locator(".dossier-page").evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);

  const accessibility = await new AxeBuilder({ page })
    .include(".dossier-page")
    .withTags(["wcag2a", "wcag2aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});

test("expired server-side session returns to the login gate", async ({ page }) => {
  await installRuntime(page, false);

  await page.goto("/");
  await expect(page.getByRole("button", { name: "Đăng nhập" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" })).not.toBeVisible();
});

test("authenticated refresh reconnects one active run from its last event without duplication", async ({ page }) => {
  await installRuntime(page);
  await page.addInitScript(({ threadId, runId }) => {
    window.sessionStorage.setItem("nexus-thread-id", threadId);
    window.sessionStorage.setItem("nexus-active-run-v1", JSON.stringify({
      threadId,
      runId,
      lastEventId: "event-41",
    }));
  }, { threadId: THREAD_ID, runId: RUN_ID });

  const finalAnswer = "Run Qlib đã nối lại và hoàn tất đúng một lần.";
  let stateReads = 0;
  let streamJoins = 0;
  let lastEventId = "";
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (await fulfillChatFoundation(route, url)) return;
    if (url.pathname === `/api/threads/${THREAD_ID}/state`) {
      stateReads += 1;
      return route.fulfill({ json: threadState(stateReads === 1 ? [
        { id: "message-user", type: "human", content: "Tiếp tục Qlib run đang chạy." },
      ] : [
        { id: "message-user", type: "human", content: "Tiếp tục Qlib run đang chạy." },
        { id: "message-final", type: "ai", content: finalAnswer },
      ]) });
    }
    if (url.pathname === `/api/threads/${THREAD_ID}/runs/${RUN_ID}/stream`) {
      streamJoins += 1;
      lastEventId = request.headers()["last-event-id"] || "";
      return route.fulfill({
        contentType: "text/event-stream",
        body: stream([{
          id: "event-42",
          event: "messages-tuple",
          data: [{ type: "AIMessageChunk", content: finalAnswer }, { langgraph_node: "supervisor" }],
        }]),
      });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found in reconnect fixture." } });
  });

  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await expect(page.getByText(finalAnswer, { exact: true })).toHaveCount(1);
  await expect.poll(() => stateReads).toBe(2);
  expect(streamJoins).toBe(1);
  expect(lastEventId).toBe("event-41");
  expect(await page.evaluate(() => window.sessionStorage.getItem("nexus-active-run-v1")))
    .toBeNull();
});

test("permission mode creates and revokes one actor-thread authorization lease", async ({ page }) => {
  await installRuntime(page);
  const leaseId = `azl_v1_${"d".repeat(32)}`;
  const leaseBodies = [];
  let revocations = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (await fulfillChatFoundation(route, url)) return;
    if (url.pathname === "/api/threads" && request.method() === "POST") {
      return route.fulfill({ json: { thread_id: THREAD_ID } });
    }
    if (url.pathname === "/api/v1/authorization-leases" && request.method() === "POST") {
      const body = request.postDataJSON();
      leaseBodies.push(body);
      return route.fulfill({ json: {
        lease_id: leaseId,
        thread_id: THREAD_ID,
        mode: body.mode,
        allow_sensitive: body.allow_sensitive,
        allowed_tools: [
          "fetch_factor_snapshot",
          "prepare_qlib_dataset",
          "record_experiment_interpretation",
          "run_governed_qlib_experiment",
        ],
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      } });
    }
    if (url.pathname === `/api/v1/authorization-leases/${leaseId}` && request.method() === "DELETE") {
      revocations += 1;
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found in permission fixture." } });
  });

  await page.goto("/chat");
  const mode = page.getByRole("combobox", { name: "Chế độ permission" });
  await mode.selectOption("autonomous");
  await expect(mode).toHaveValue("autonomous");
  await expect(page.getByRole("button", { name: "Tắt quyền tự chủ" })).toBeVisible();
  expect(leaseBodies).toEqual([{
    thread_id: THREAD_ID,
    mode: "autonomous",
    ttl_seconds: 3600,
    allow_sensitive: false,
  }]);

  await page.getByRole("button", { name: "Tắt quyền tự chủ" }).click();
  await expect(mode).toHaveValue("safe");
  expect(revocations).toBe(1);
});

test("authenticated approval flow rejects one exact action then approves a new action once", async ({ page }) => {
  await installRuntime(page);
  const streamBodies = [];
  let promptCount = 0;
  let resumeCount = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (await fulfillChatFoundation(route, url)) return;
    if (url.pathname === "/api/threads" && request.method() === "POST") {
      return route.fulfill({ json: { thread_id: THREAD_ID } });
    }
    if (url.pathname === `/api/threads/${THREAD_ID}/runs/stream` && request.method() === "POST") {
      const body = request.postDataJSON();
      streamBodies.push(body);
      if (body.input) {
        promptCount += 1;
        const approvedCandidate = promptCount === 2;
        return route.fulfill({
          contentType: "text/event-stream",
          headers: {
            "content-location": `/threads/${THREAD_ID}/runs/pending-${promptCount}`,
          },
          body: stream([{
            id: `approval-${promptCount}`,
            event: "updates",
            data: {
              __interrupt__: [{
                value: {
                  action_requests: [{
                    name: "publish_ai_trader_strategy",
                    args: {
                      symbol: approvedCandidate ? "MSFT" : "AAPL",
                      action_digest: approvedCandidate ? "digest-msft-v2" : "digest-aapl-v1",
                    },
                  }],
                },
              }],
            },
          }]),
        });
      }
      resumeCount += 1;
      const decision = body.command?.resume?.decisions?.[0]?.type;
      const answer = decision === "approve"
        ? "Tín hiệu MSFT mới đã được phê duyệt và xuất bản đúng một lần."
        : "Tín hiệu AAPL ban đầu đã bị từ chối và không được xuất bản.";
      return route.fulfill({
        contentType: "text/event-stream",
        headers: {
          "content-location": `/threads/${THREAD_ID}/runs/resume-${resumeCount}`,
        },
        body: stream([{
          id: `resume-event-${resumeCount}`,
          event: "messages-tuple",
          data: [{ type: "AIMessageChunk", content: answer }, { langgraph_node: "supervisor" }],
        }]),
      });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found in approval fixture." } });
  });

  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  const input = page.getByPlaceholder("Nhập yêu cầu cho Nexus…");
  await input.fill("Xuất bản tín hiệu AAPL chính xác này.");
  await page.getByRole("button", { name: "Gửi", exact: true }).click();
  await expect(page.getByText("digest-aapl-v1", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Từ chối", exact: true }).click();
  await expect(page.getByText(
    "Tín hiệu AAPL ban đầu đã bị từ chối và không được xuất bản.",
    { exact: true },
  )).toBeVisible();

  await input.fill("Xuất bản tín hiệu MSFT mới với action digest mới.");
  await page.getByRole("button", { name: "Gửi", exact: true }).click();
  await expect(page.getByText("digest-msft-v2", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Phê duyệt", exact: true }).click();
  await expect(page.getByText(
    "Tín hiệu MSFT mới đã được phê duyệt và xuất bản đúng một lần.",
    { exact: true },
  )).toHaveCount(1);
  await expect(page.locator(".chat-run-strip")).toContainText("Hành động đã được xử lý.");
  await expect(page.locator(".trace-event.waiting")).toHaveCount(0);

  const decisions = streamBodies
    .filter((body) => body.command)
    .map((body) => body.command.resume.decisions[0].type);
  expect(decisions).toEqual(["reject", "approve"]);
  expect(streamBodies.filter((body) => body.command?.resume?.decisions?.[0]?.type === "approve"))
    .toHaveLength(1);
});
