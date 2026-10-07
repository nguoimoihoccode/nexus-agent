import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

import {
  ProductApiError,
  normalizeComparison,
  normalizeDossier,
  fetchExperimentCatalog,
  normalizeTopology,
} from "./governedQuant.js";

const experimentId = `exp_v1_${"a".repeat(32)}`;

function dossier(overrides = {}) {
  return {
    schema_version: "1",
    experiment: { experiment_id: experimentId },
    dataset: null,
    metrics: [],
    artifacts: [],
    attempts: [],
    publications: [],
    interpretations: [],
    sources: [],
    evidence_gaps: [],
    limitations: { production_eligibility: "research_only" },
    ...overrides,
  };
}

test("production eligibility is derived only from typed limitations", () => {
  const normalized = normalizeDossier(dossier({
    production_eligibility: "eligible",
    injected_html: "<img src=x onerror=alert(1)>",
    experiment: { experiment_id: experimentId, unexpected_authority: true },
    limitations: { production_eligibility: "research_only" },
  }));

  assert.equal(normalized.typedProductionEligibility, "research_only");
  assert.equal("injected_html" in normalized, false);
  assert.equal("unexpected_authority" in normalized.experiment, false);
});

test("versioned backend contract fixture remains accepted", () => {
  const fixture = JSON.parse(readFileSync(
    new URL("../features/dossier/fixtures/dossier-v1.json", import.meta.url),
    "utf8",
  ));

  assert.equal(normalizeDossier(fixture).typedProductionEligibility, "research_only");
});

test("unknown or prose eligibility fails closed", () => {
  const normalized = normalizeDossier(dossier({
    limitations: { production_eligibility: "production ready because metrics look good" },
  }));

  assert.equal(normalized.typedProductionEligibility, "blocked");
});

test("dossier rejects unsupported schema and malformed evidence arrays", () => {
  assert.throws(
    () => normalizeDossier(dossier({ schema_version: "2" })),
    (error) => error instanceof ProductApiError && error.code === "invalid_contract",
  );
  assert.throws(() => normalizeDossier(dossier({ metrics: ["unsafe"] })));
});

test("comparison preserves backend compatibility decision", () => {
  const result = normalizeComparison({
    schema_version: "1",
    compatible: false,
    configurations: [],
    metrics: [],
    incompatibilities: [{ code: "dataset_revision_mismatch", message: "Different data" }],
  });

  assert.equal(result.compatible, false);
  assert.equal(result.incompatibilities[0].code, "dataset_revision_mismatch");
});

test("topology projection accepts presentation data and ignores unknown agents", () => {
  const result = normalizeTopology({
    schema_version: "1",
    agents: {
      supervisor: { enabled: true, skills: [], title: "Supervisor" },
      "../unsafe": { enabled: true, skills: [] },
    },
    skills: {},
    resource_edges: [{ from: "supervisor", to: "checkpoint", kind: "uses" }],
  });

  assert.deepEqual(Object.keys(result.agents), ["supervisor"]);
  assert.equal(result.resource_edges.length, 1);
});

test("experiment catalog validates the versioned summary contract", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    assert.match(String(input), /\/v1\/experiments\?limit=10&status=completed/);
    return new Response(JSON.stringify({
      schema_version: "1",
      items: [{
        experiment_id: experimentId,
        dataset_revision_id: "dsr_v1_catalog",
        status: "completed",
        progress_stage: null,
        created_at: "2026-07-20T00:00:00+00:00",
        updated_at: "2026-07-20T00:01:00+00:00",
        specification_summary: {
          universe: "csi300",
          model: "linear",
          feature_set: "Alpha158",
          strategy_type: "topk_dropout",
          test_start: "2025-01-01",
          test_end: "2025-03-31",
        },
      }],
      next_cursor: "next-page",
    }), { status: 200, headers: { "content-type": "application/json" } });
  };

  try {
    const page = await fetchExperimentCatalog({ limit: 10, statuses: ["completed"] });
    assert.equal(page.items[0]?.experiment_id, experimentId);
    assert.equal(page.next_cursor, "next-page");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("product API rejects an oversized JSON response before parsing it", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("{}", {
    status: 200,
    headers: {
      "content-type": "application/json",
      "content-length": String(4 * 1024 * 1024 + 1),
    },
  });

  try {
    await assert.rejects(
      fetchExperimentCatalog(),
      (error) => error instanceof ProductApiError && error.code === "response_too_large",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
