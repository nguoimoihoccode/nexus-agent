import { z } from "zod";

import { authenticatedHeaders, notifyAuthExpired } from "../auth/session.js";

const API_URL = import.meta.env.VITE_LANGGRAPH_API_URL || "/api";
const EXPERIMENT_ID = /^exp_v1_[0-9a-f]{32}$/;
const ARTIFACT_ID = /^art_v1_[0-9a-f]{24}$/;
const NODE_ID = /^[A-Za-z0-9._:-]{1,200}$/;
const MAX_PRODUCT_JSON_BYTES = 4 * 1024 * 1024;
const MAX_ERROR_BYTES = 64 * 1024;
const MAX_COLLECTION_ITEMS = 500;
const identifierSchema = z.string().min(1).max(200);
const shortTextSchema = z.string().max(512);
const longTextSchema = z.string().max(16 * 1024);
const experimentStatusSchema = z.enum([
  "queued",
  "leased",
  "running",
  "cancelling",
  "cancelled",
  "completed",
  "failed",
  "expired",
]);
const eligibilitySchema = z.enum(["blocked", "research_only", "eligible"]);
const jsonRecordSchema = z.record(z.string().max(200), z.unknown());
const evidenceGapSchema = z.object({ code: shortTextSchema, message: longTextSchema });
const transitionSchema = z.object({
  sequence: z.number().int().nonnegative(),
  from_state: z.unknown().optional(),
  to_state: shortTextSchema,
  stage: z.unknown().optional(),
});
const workflowSchema = z.object({
  workflow_id: identifierSchema.optional(),
  state: shortTextSchema.optional(),
  stage: shortTextSchema.optional(),
  thread_id: identifierSchema.optional(),
  run_id: identifierSchema.optional(),
  normalized_input: z.unknown().optional(),
  transitions: z.array(transitionSchema).max(MAX_COLLECTION_ITEMS).optional(),
});
const experimentSchema = z.object({
  experiment_id: z.string().regex(EXPERIMENT_ID),
  status: shortTextSchema.optional(),
  specification_digest: identifierSchema.optional(),
  specification: jsonRecordSchema.optional(),
  error: z.object({ message: longTextSchema.optional(), code: shortTextSchema.optional() }).optional(),
});
const datasetSchema = z.object({
  dataset_revision_id: identifierSchema.optional(),
  source_staging_revision_id: identifierSchema.optional(),
  manifest_hash: identifierSchema.optional(),
  schema_version: z.unknown().optional(),
  status: shortTextSchema.optional(),
  ready_at: shortTextSchema.optional(),
  files: z.array(z.unknown()).max(MAX_COLLECTION_ITEMS).optional(),
});
const metricSchema = z.object({
  metric_id: identifierSchema,
  metric_group: z.unknown().optional(),
  name: shortTextSchema,
  value: z.unknown().optional(),
  unit: z.unknown().optional(),
  meaning: z.unknown().optional(),
  calculation_version: z.unknown().optional(),
  evidence_status: shortTextSchema,
  missing_evidence: z.array(shortTextSchema).max(MAX_COLLECTION_ITEMS).optional(),
});
const artifactSchema = z.object({
  artifact_id: identifierSchema.optional(),
  artifact_type: shortTextSchema.optional(),
  content_hash: identifierSchema,
  storage_status: shortTextSchema,
  download_eligible: z.boolean().optional(),
});
const attemptSchema = z.object({
  attempt: z.union([shortTextSchema, z.number().int().nonnegative()]),
  status: shortTextSchema,
  stage: z.unknown().optional(),
  runtime_versions: z.unknown().optional(),
});
const publicationSchema = z.object({
  publication_id: identifierSchema,
  publication_type: shortTextSchema,
  status: shortTextSchema,
  approval: z.object({
    status: shortTextSchema.optional(),
    execution_status: z.unknown().optional(),
    approval_request_id: identifierSchema.optional(),
  }).optional(),
});
const interpretationSchema = z.object({
  interpretation_report_id: identifierSchema,
  content: z.unknown().optional(),
  evidence_references: z.array(identifierSchema).max(MAX_COLLECTION_ITEMS).optional(),
});
const sourceSchema = z.object({
  source_record_id: identifierSchema,
  locator: z.unknown().optional(),
  retrieval_status: shortTextSchema,
  content_hash: identifierSchema,
  metadata: z.object({ title: longTextSchema.optional() }).optional(),
});
const dossierSchema = z.object({
  schema_version: z.literal("1"),
  experiment: experimentSchema,
  dataset: datasetSchema.nullish(),
  workflow: workflowSchema.nullish(),
  lineage: z.object({ root_node_id: identifierSchema.optional() }).nullish(),
  metrics: z.array(metricSchema).max(MAX_COLLECTION_ITEMS),
  artifacts: z.array(artifactSchema).max(MAX_COLLECTION_ITEMS),
  attempts: z.array(attemptSchema).max(MAX_COLLECTION_ITEMS),
  publications: z.array(publicationSchema).max(MAX_COLLECTION_ITEMS),
  interpretations: z.array(interpretationSchema).max(MAX_COLLECTION_ITEMS),
  sources: z.array(sourceSchema).max(MAX_COLLECTION_ITEMS),
  evidence_gaps: z.array(evidenceGapSchema).max(MAX_COLLECTION_ITEMS),
  limitations: jsonRecordSchema.optional().default({}),
});
const comparisonIssueSchema = z.object({ code: shortTextSchema, message: longTextSchema });
const comparisonSchema = z.object({
  schema_version: z.literal("1"),
  compatible: z.boolean(),
  configurations: z.array(z.object({ experiment_id: z.string().regex(EXPERIMENT_ID) })).max(5),
  metrics: z.array(z.object({
    metric_group: shortTextSchema,
    name: shortTextSchema,
    meaning: longTextSchema.optional(),
    values: z.unknown().optional(),
  })).max(MAX_COLLECTION_ITEMS),
  incompatibilities: z.array(comparisonIssueSchema).max(MAX_COLLECTION_ITEMS),
});
const topologySchema = z.object({
  schema_version: z.literal("1"),
  agents: z.record(z.string(), z.object({
    enabled: z.boolean(),
    skills: z.array(identifierSchema).max(MAX_COLLECTION_ITEMS),
    title: shortTextSchema.optional(),
    description: longTextSchema.optional(),
    tool_labels: z.array(shortTextSchema).max(MAX_COLLECTION_ITEMS).optional(),
  })),
  skills: z.record(z.string(), jsonRecordSchema),
  resource_edges: z.array(z.object({ from: identifierSchema, to: identifierSchema })).max(MAX_COLLECTION_ITEMS),
});
const lineageSchema = z.object({
  nodes: z.array(z.object({ node_id: identifierSchema, node_type: shortTextSchema })).max(MAX_COLLECTION_ITEMS),
  edges: z.array(jsonRecordSchema).max(MAX_COLLECTION_ITEMS),
});
const experimentSummarySchema = z.object({
  universe: shortTextSchema.nullable(),
  model: shortTextSchema.nullable(),
  feature_set: shortTextSchema.nullable(),
  strategy_type: shortTextSchema.nullable(),
  test_start: shortTextSchema.nullable(),
  test_end: shortTextSchema.nullable(),
});
const experimentCatalogPageSchema = z.object({
  schema_version: z.literal("1"),
  items: z.array(z.object({
    experiment_id: z.string().regex(EXPERIMENT_ID),
    dataset_revision_id: identifierSchema,
    status: experimentStatusSchema,
    progress_stage: shortTextSchema.nullable(),
    created_at: shortTextSchema.min(1),
    updated_at: shortTextSchema.min(1),
    specification_summary: experimentSummarySchema,
  })).max(100),
  next_cursor: z.string().min(1).max(2048).nullable(),
});

export type ProductTopology = z.infer<typeof topologySchema>;
export type ExperimentDossier = z.infer<typeof dossierSchema> & {
  typedProductionEligibility: z.infer<typeof eligibilitySchema>;
};
export type ExperimentComparison = z.infer<typeof comparisonSchema>;
export type LineageGraph = z.infer<typeof lineageSchema>;
export type ExperimentStatus = z.infer<typeof experimentStatusSchema>;
export type ExperimentCatalogPage = z.infer<typeof experimentCatalogPageSchema>;
export type ExperimentCatalogItem = ExperimentCatalogPage["items"][number];
export type DossierArtifact = z.infer<typeof artifactSchema>;
export type DownloadPayload = { blob: Blob; filename: string };

export function isExperimentId(value: string): boolean {
  return EXPERIMENT_ID.test(value);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Product API request failed.";
}

export class ProductApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code = "request_failed") {
    super(message);
    this.name = "ProductApiError";
    this.status = status;
    this.code = code;
  }
}

async function headers(extra: HeadersInit = {}): Promise<HeadersInit> {
  return authenticatedHeaders(extra);
}

async function responseErrorMessage(response: Response): Promise<string> {
  if (response.status === 401) notifyAuthExpired();
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_ERROR_BYTES) {
    return `Backend error response vượt quá giới hạn (${response.status}).`;
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_ERROR_BYTES) {
    return `Backend error response vượt quá giới hạn (${response.status}).`;
  }
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object") {
      const detail = "detail" in parsed ? parsed.detail : "message" in parsed ? parsed.message : null;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // The plain response body is the safest fallback.
  }
  return body || `${response.status} ${response.statusText}`;
}

async function request(path: string, options: { signal?: AbortSignal; accept?: string } = {}): Promise<Response> {
  const init: RequestInit = {
    headers: await headers({ Accept: options.accept ?? "application/json" }),
    cache: "no-store",
  };
  if (options.signal) init.signal = options.signal;
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) throw new ProductApiError(await responseErrorMessage(response), response.status);
  return response;
}

async function readJsonResponse(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (contentType !== "application/json" && !contentType?.endsWith("+json")) {
    throw new ProductApiError("Backend trả về định dạng nội dung không hợp lệ.", 502, "invalid_contract");
  }
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_PRODUCT_JSON_BYTES) {
    throw new ProductApiError("Backend response vượt quá giới hạn 4 MiB.", 502, "response_too_large");
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_PRODUCT_JSON_BYTES) {
    throw new ProductApiError("Backend response vượt quá giới hạn 4 MiB.", 502, "response_too_large");
  }
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new ProductApiError("Backend trả về JSON không hợp lệ.", 502, "invalid_contract");
  }
}

function parseContract<T>(schema: z.ZodType<T>, payload: unknown, message: string): T {
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new ProductApiError(message, 502, "invalid_contract");
  }
  return parsed.data;
}

export function normalizeDossier(payload: unknown): ExperimentDossier {
  const dossier = parseContract(dossierSchema, payload, "Unsupported or invalid dossier contract.");
  const eligibility = eligibilitySchema.safeParse(dossier.limitations.production_eligibility);
  return {
    ...dossier,
    typedProductionEligibility: eligibility.success ? eligibility.data : "blocked",
  };
}

export function normalizeComparison(payload: unknown): ExperimentComparison {
  return parseContract(comparisonSchema, payload, "Unsupported experiment comparison contract.");
}

export function normalizeTopology(payload: unknown): ProductTopology {
  const parsed = parseContract(topologySchema, payload, "Unsupported runtime topology contract.");
  const agents = Object.fromEntries(Object.entries(parsed.agents).filter(([name]) => (
    /^[a-z0-9][a-z0-9-]{1,63}$/.test(name)
  )));
  return { ...parsed, agents };
}

export async function fetchProductTopology(signal?: AbortSignal): Promise<ProductTopology> {
  const response = await request("/v1/topology", signal ? { signal } : {});
  return normalizeTopology(await readJsonResponse(response));
}

export async function fetchExperimentCatalog({
  limit = 20,
  statuses = [],
  cursor,
  signal,
}: {
  limit?: number;
  statuses?: ExperimentStatus[];
  cursor?: string | null;
  signal?: AbortSignal;
} = {}): Promise<ExperimentCatalogPage> {
  const query = new URLSearchParams({ limit: String(limit) });
  statuses.forEach((status) => query.append("status", status));
  if (cursor) query.set("cursor", cursor);
  const response = await request(`/v1/experiments?${query}`, signal ? { signal } : {});
  return parseContract(
    experimentCatalogPageSchema,
    await readJsonResponse(response),
    "Unsupported experiment catalog contract.",
  );
}

export async function fetchExperimentDossier(experimentId: string, signal?: AbortSignal): Promise<ExperimentDossier> {
  if (!isExperimentId(experimentId)) throw new ProductApiError("Experiment ID không hợp lệ.", 422, "invalid_id");
  const response = await request(`/v1/experiments/${encodeURIComponent(experimentId)}/dossier`, signal ? { signal } : {});
  return normalizeDossier(await readJsonResponse(response));
}

export async function fetchExperimentComparison(experimentIds: string[], signal?: AbortSignal): Promise<ExperimentComparison> {
  const ids = [...new Set(experimentIds)];
  if (ids.length < 2 || ids.length > 5 || ids.some((id) => !isExperimentId(id))) {
    throw new ProductApiError("Cần từ 2 đến 5 experiment ID hợp lệ.", 422, "invalid_id");
  }
  const query = new URLSearchParams();
  ids.forEach((id) => query.append("experiment_ids", id));
  const response = await request(`/v1/experiments/compare?${query}`, signal ? { signal } : {});
  return normalizeComparison(await readJsonResponse(response));
}

export async function fetchLineage(nodeId: string, signal?: AbortSignal): Promise<LineageGraph> {
  if (!NODE_ID.test(nodeId)) throw new ProductApiError("Lineage node ID không hợp lệ.", 422, "invalid_id");
  const response = await request(`/v1/lineage/${encodeURIComponent(nodeId)}?direction=both&depth=4&limit=500`, signal ? { signal } : {});
  return parseContract(lineageSchema, await readJsonResponse(response), "Invalid lineage graph contract.");
}

async function download(path: string, fallbackName: string): Promise<DownloadPayload> {
  const response = await request(path, { accept: "application/octet-stream" });
  const disposition = response.headers.get("content-disposition") || "";
  const matched = disposition.match(/filename="([A-Za-z0-9._-]+)"/);
  return { blob: await response.blob(), filename: matched?.[1] ?? fallbackName };
}

export async function exportExperimentDossier(experimentId: string): Promise<DownloadPayload> {
  if (!isExperimentId(experimentId)) throw new ProductApiError("Experiment ID không hợp lệ.", 422, "invalid_id");
  return download(`/v1/experiments/${encodeURIComponent(experimentId)}/export`, `${experimentId}-dossier-v1.json`);
}

export async function downloadArtifact(artifactId: string): Promise<DownloadPayload> {
  if (!ARTIFACT_ID.test(artifactId)) throw new ProductApiError("Artifact ID không hợp lệ.", 422, "invalid_id");
  return download(`/v1/artifacts/${encodeURIComponent(artifactId)}/content`, `${artifactId}.bin`);
}

export function saveDownload({ blob, filename }: DownloadPayload): void {
  try {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  } catch (error) {
    throw new ProductApiError(errorMessage(error), 500, "download_failed");
  }
}
