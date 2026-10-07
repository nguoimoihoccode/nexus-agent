import { type ElementType, type ReactNode, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Tabs } from "radix-ui";
import type { LucideIcon } from "lucide-react";
import {
  ChartNoAxesCombined,
  CheckCircle2,
  ArrowLeft,
  Clock3,
  Database,
  Download,
  FileArchive,
  FileSearch,
  GitCompare,
  Link2,
  LoaderCircle,
  ShieldAlert,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";

import {
  ProductApiError,
  type DossierArtifact,
  type ExperimentComparison,
  type ExperimentDossier,
  type LineageGraph,
  type DownloadPayload,
  downloadArtifact,
  exportExperimentDossier,
  fetchExperimentComparison,
  fetchExperimentDossier,
  fetchLineage,
  isExperimentId,
  saveDownload,
} from "../../api/governedQuant.js";
import { paginateEvidence } from "./model/evidencePagination.js";

type EvidenceViewState = "idle" | "loading" | "empty" | "expired" | "unauthorized" | "failed" | "ready";

function viewState(error: unknown): EvidenceViewState {
  if (error instanceof ProductApiError && (error.status === 401 || error.status === 403)) return "unauthorized";
  if (error instanceof ProductApiError && error.status === 404) return "empty";
  if (error instanceof ProductApiError && error.status === 410) return "expired";
  return "failed";
}

function display(value: unknown, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function safeSourceUrl(value: unknown) {
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function stateClass(value: unknown, allowed: readonly string[]) {
  return typeof value === "string" && allowed.includes(value) ? value : "unknown";
}

function JsonEvidence({ value }: { value: unknown }) {
  return <pre className="evidence-json">{JSON.stringify(value ?? {}, null, 2)}</pre>;
}

function EvidenceState({ state, message }: { state: Exclude<EvidenceViewState, "ready">; message?: string }) {
  const contents = {
    idle: [FileSearch, "Nhập experiment ID", "Dossier chỉ hiển thị evidence đã được backend xác thực."],
    loading: [LoaderCircle, "Đang tải dossier", "Đang đọc evidence và lineage theo actor hiện tại."],
    empty: [FileSearch, "Không tìm thấy evidence", message || "Experiment không tồn tại hoặc không thuộc actor hiện tại."],
    expired: [Clock3, "Evidence đã hết hạn", message || "Metadata tombstone vẫn còn nhưng nội dung không còn khả dụng."],
    unauthorized: [ShieldAlert, "Không có quyền truy cập", message || "Tài khoản hiện tại không được phép đọc quant evidence."],
    failed: [TriangleAlert, "Không thể tải dossier", message || "Product API trả về lỗi có cấu trúc."],
  } satisfies Record<Exclude<EvidenceViewState, "ready">, readonly [LucideIcon, string, string]>;
  const content = contents[state];
  const Icon = content[0];
  return (
    <section className={`evidence-state ${state}`} role={state === "failed" ? "alert" : "status"}>
      <Icon className={state === "loading" ? "spin" : ""} size={28} />
      <strong>{content[1]}</strong>
      <p>{content[2]}</p>
    </section>
  );
}

function EvidenceSection({ icon: Icon, title, subtitle, children }: {
  icon: LucideIcon;
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <section className="evidence-panel">
      <header>
        <Icon size={17} />
        <div><h3>{title}</h3><span>{subtitle}</span></div>
      </header>
      <div className="evidence-panel-body">{children}</div>
    </section>
  );
}

function KeyValues({ items }: { items: ReadonlyArray<readonly [string, unknown]> }) {
  return (
    <dl className="evidence-kv">
      {items.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{display(value)}</dd></div>
      ))}
    </dl>
  );
}

function EvidenceCollection<T>({
  items,
  label,
  as = "div",
  className,
  containerLabel,
  emptyMessage,
  renderItem,
}: {
  items: readonly T[];
  label: string;
  as?: "div" | "ul" | "ol";
  className?: string;
  containerLabel?: string;
  emptyMessage: string;
  renderItem: (item: T) => ReactNode;
}) {
  const [requestedPage, setRequestedPage] = useState(1);
  const pagination = paginateEvidence(items, requestedPage);
  const Container = as as ElementType;

  useEffect(() => setRequestedPage(1), [items]);

  if (pagination.totalItems === 0) return <p className="empty-copy">{emptyMessage}</p>;

  return <>
    <Container
      className={className}
      aria-label={containerLabel}
      tabIndex={containerLabel ? 0 : undefined}
    >{pagination.items.map(renderItem)}</Container>
    {pagination.totalPages > 1 && (
      <nav className="evidence-pagination" aria-label={`${label} pagination`}>
        <button
          type="button"
          aria-label={`Previous ${label} page`}
          disabled={pagination.page === 1}
          onClick={() => setRequestedPage(pagination.page - 1)}
        >Previous</button>
        <span aria-live="polite">
          {pagination.startItem}–{pagination.endItem} of {pagination.totalItems}
          <small>Page {pagination.page} of {pagination.totalPages}</small>
        </span>
        <button
          type="button"
          aria-label={`Next ${label} page`}
          disabled={pagination.page === pagination.totalPages}
          onClick={() => setRequestedPage(pagination.page + 1)}
        >Next</button>
      </nav>
    )}
  </>;
}

function DossierView({ dossier, onExport, onArtifact, exportState }: {
  dossier: ExperimentDossier;
  onExport: () => void;
  onArtifact: (artifact: DossierArtifact) => void;
  exportState: "idle" | "loading" | "ready" | "failed";
}) {
  const [lineageEnabled, setLineageEnabled] = useState(false);
  const experiment = dossier.experiment;
  const dataset = dossier.dataset;
  const specification = experiment.specification || {};
  const partial = dossier.evidence_gaps.length > 0;

  const lineage = useQuery<LineageGraph>({
    queryKey: ["lineage", dossier.lineage?.root_node_id || experiment.experiment_id],
    queryFn: ({ signal }) => fetchLineage(dossier.lineage?.root_node_id || experiment.experiment_id, signal),
    enabled: lineageEnabled,
    retry: false,
  });

  return (
    <div className="dossier-content">
      <section className="dossier-hero">
        <div>
          <span className="eyebrow">Experiment dossier · schema v{dossier.schema_version}</span>
          <h2>{experiment.experiment_id}</h2>
          <div className="dossier-badges">
            <span className={`state-badge ${stateClass(experiment.status, ["queued", "leased", "running", "cancelling", "cancelled", "completed", "failed", "expired"])}`}>{experiment.status}</span>
            <span className={`eligibility-badge ${dossier.typedProductionEligibility}`}>
              {dossier.typedProductionEligibility === "eligible" ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
              {dossier.typedProductionEligibility}
            </span>
            {partial && <span className="state-badge partial">partial evidence</span>}
          </div>
        </div>
        <button type="button" className="primary-action" onClick={onExport} disabled={exportState === "loading"}>
          <Download size={15} /> {exportState === "loading" ? "Đang xuất…" : "Export bundle"}
        </button>
      </section>

      {partial && (
        <section className="evidence-gaps" aria-label="Evidence gaps">
          <TriangleAlert size={17} />
          <div><strong>Evidence chưa hoàn chỉnh</strong>{dossier.evidence_gaps.map((gap) => <p key={gap.code}><code>{gap.code}</code> {gap.message}</p>)}</div>
        </section>
      )}

      <div className="evidence-grid">
        <EvidenceSection icon={FileSearch} title="Request & workflow" subtitle="Original intent và normalized execution spec">
          <KeyValues items={[
            ["Workflow", dossier.workflow?.workflow_id],
            ["State / stage", `${display(dossier.workflow?.state)} / ${display(dossier.workflow?.stage)}`],
            ["Thread", dossier.workflow?.thread_id],
            ["Run", dossier.workflow?.run_id],
            ["Spec digest", experiment.specification_digest],
            ["Failure", experiment.error?.message || experiment.error?.code],
          ]} />
          <h4>Normalized input</h4><JsonEvidence value={dossier.workflow?.normalized_input || specification} />
          {dossier.workflow?.transitions && <EvidenceCollection
            items={dossier.workflow.transitions}
            label="Workflow events"
            as="ol"
            className="evidence-list"
            emptyMessage="No workflow transitions recorded."
            renderItem={(item) => <li key={item.sequence}><code>#{item.sequence}</code> {display(item.from_state)} → <strong>{item.to_state}</strong> · {display(item.stage)}</li>}
          />}
        </EvidenceSection>

        <EvidenceSection icon={Database} title="Dataset revision" subtitle="Immutable manifest, validation và financial semantics">
          {dataset ? <>
            <KeyValues items={[
              ["Revision", dataset.dataset_revision_id],
              ["Staging revision", dataset.source_staging_revision_id],
              ["Manifest hash", dataset.manifest_hash],
              ["Schema / status", `${display(dataset.schema_version)} / ${display(dataset.status)}`],
              ["Ready at", dataset.ready_at],
              ["Files", dataset.files?.length || 0],
            ]} />
            <h4>Typed limitations</h4><JsonEvidence value={dossier.limitations} />
          </> : <p className="empty-copy">Dataset evidence is missing.</p>}
        </EvidenceSection>

        <EvidenceSection icon={ChartNoAxesCombined} title="Experiment specification" subtitle="Segments, model, features, strategy và cost">
          <KeyValues items={[
            ["Universe", specification.universe],
            ["Frequency", specification.frequency],
            ["Model", specification.model],
            ["Feature set", specification.feature_set],
            ["Strategy", specification.strategy],
            ["Train", specification.train],
            ["Validation", specification.valid],
            ["Test", specification.test],
            ["Label horizon", specification.label_horizon],
            ["Cost", specification.cost],
          ]} />
          <h4>Runtime attempts</h4>
          {!dossier.attempts.length ? <p className="empty-copy">No attempts recorded.</p> : dossier.attempts.map((attempt) => <article className="attempt-row" key={attempt.attempt}><strong>Attempt {attempt.attempt}</strong><span>{attempt.status} · {display(attempt.stage)}</span><JsonEvidence value={attempt.runtime_versions} /></article>)}
        </EvidenceSection>

        <EvidenceSection icon={ChartNoAxesCombined} title="Metric evidence" subtitle="Definition, calculation version và lineage references">
          {!dossier.metrics.length ? <p className="empty-copy">No metric evidence recorded.</p> : (
            <div className="metric-evidence-table">
              {dossier.metrics.map((metric) => <article key={metric.metric_id} className={metric.evidence_status}>
                <div><strong>{metric.name}</strong><span>{display(metric.metric_group)} · {display(metric.unit)}</span></div>
                <em>{display(metric.value)}</em>
                <p>{display(metric.meaning, "Definition missing")}</p>
                <small>calc {display(metric.calculation_version)} · {metric.evidence_status}</small>
                {(metric.missing_evidence?.length ?? 0) > 0 && <code>missing: {metric.missing_evidence?.join(", ")}</code>}
              </article>)}
            </div>
          )}
        </EvidenceSection>

        <EvidenceSection icon={Link2} title="Sources & interpretation" subtitle="Retrieval state, evidence links và model-assisted findings">
          <EvidenceCollection
            items={dossier.sources}
            label="Sources"
            as="ul"
            className="source-list"
            emptyMessage="No linked research sources."
            renderItem={(source) => {
              const url = safeSourceUrl(source.locator);
              return <li key={source.source_record_id}>{url ? <a href={url} target="_blank" rel="noreferrer">{source.metadata?.title || url}</a> : <strong>{source.metadata?.title || "Unsafe source locator hidden"}</strong>}<span>{source.retrieval_status} · {source.content_hash}</span></li>;
            }}
          />
          {dossier.interpretations.map((report) => <article className="interpretation-card" key={report.interpretation_report_id}><strong>{report.interpretation_report_id}</strong><JsonEvidence value={report.content} /><small>Evidence: {(report.evidence_references || []).join(", ")}</small></article>)}
        </EvidenceSection>

        <EvidenceSection icon={FileArchive} title="Artifacts" subtitle="Hash, retention state và safe binary download">
          <EvidenceCollection
            items={dossier.artifacts}
            label="Artifacts"
            className="artifact-list"
            emptyMessage="No artifact metadata."
            renderItem={(artifact) => <article key={artifact.artifact_id || artifact.content_hash}>
              <div><strong>{artifact.artifact_type || artifact.artifact_id}</strong><code>{artifact.content_hash}</code></div>
              <span className={`state-badge ${stateClass(artifact.storage_status, ["ready", "expired", "deleted", "corrupt"])}`}>{artifact.storage_status}</span>
              <button type="button" onClick={() => onArtifact(artifact)} disabled={artifact.storage_status !== "ready" || artifact.download_eligible !== true}><Download size={14} /> Download</button>
            </article>}
          />
        </EvidenceSection>

        <EvidenceSection icon={ShieldCheck} title="Approvals & publications" subtitle="Human decision bound to resulting effect">
          {!dossier.publications.length ? <p className="empty-copy">No publication approval is linked.</p> : dossier.publications.map((publication) => <article className="approval-row" key={publication.publication_id}><CheckCircle2 size={16} /><div><strong>{publication.publication_type} · {publication.status}</strong><span>{publication.approval?.status} / {display(publication.approval?.execution_status)}</span><small>{publication.approval?.approval_request_id}</small></div></article>)}
        </EvidenceSection>

        <EvidenceSection icon={Link2} title="Lineage explorer" subtitle="Navigate backward from metrics and forward from dataset">
          <button type="button" className="secondary-action" onClick={() => setLineageEnabled(true)} disabled={lineage.isFetching}>{lineage.isFetching ? <LoaderCircle className="spin" size={14} /> : <Link2 size={14} />} Load lineage</button>
          {lineage.isError && <p className="inline-error">{lineage.error instanceof Error ? lineage.error.message : "Không thể tải lineage."}</p>}
          {lineage.data && <><KeyValues items={[["Nodes", lineage.data.nodes.length], ["Edges", lineage.data.edges.length], ["Root", dossier.lineage?.root_node_id]]} /><EvidenceCollection
            items={lineage.data.nodes}
            label="Lineage nodes"
            as="ul"
            className="lineage-list"
            containerLabel="Visible lineage nodes"
            emptyMessage="No lineage nodes returned."
            renderItem={(node) => <li key={node.node_id}><code>{node.node_type}</code><span>{node.node_id}</span></li>}
          /></>}
        </EvidenceSection>
      </div>
    </div>
  );
}

function ComparisonView({ initialIds }: { initialIds: string[] }) {
  const navigate = useNavigate();
  const [input, setInput] = useState(initialIds.join(", "));
  const comparison = useQuery<ExperimentComparison>({
    queryKey: ["experiment-comparison", initialIds],
    queryFn: ({ signal }) => fetchExperimentComparison(initialIds, signal),
    enabled: initialIds.length >= 2,
    retry: false,
  });

  useEffect(() => setInput(initialIds.join(", ")), [initialIds]);

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const ids = [...new Set(input.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean))];
    if (ids.join("|") === initialIds.join("|")) void comparison.refetch();
    else void navigate({ to: "/evidence/compare", search: { experiments: ids }, replace: true });
  };

  const state: EvidenceViewState = comparison.data
    ? "ready"
    : comparison.isFetching
      ? "loading"
      : comparison.error
        ? viewState(comparison.error)
        : "idle";
  const message = comparison.error instanceof Error ? comparison.error.message : undefined;

  return <div className="comparison-view">
    <form className="dossier-search" onSubmit={submit}><label><span>2–5 experiment IDs</span><textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder="exp_v1_… , exp_v1_…" /></label><button type="submit" className="primary-action"><GitCompare size={15} /> Compare</button></form>
    {state !== "ready" && <EvidenceState state={state} {...(message ? { message } : {})} />}
    {comparison.data && <>
      <section className={`comparison-verdict ${comparison.data.compatible ? "compatible" : "incompatible"}`}>
        {comparison.data.compatible ? <ShieldCheck size={20} /> : <TriangleAlert size={20} />}
        <div><strong>{comparison.data.compatible ? "Comparable evidence" : "Comparison rejected"}</strong>{comparison.data.incompatibilities.map((item) => <p key={item.code}><code>{item.code}</code> {item.message}</p>)}</div>
      </section>
      <EvidenceSection icon={GitCompare} title="Configuration diff" subtitle="Shown before metric deltas"><div className="comparison-configs">{comparison.data.configurations.map((item) => <article key={item.experiment_id}><strong>{item.experiment_id}</strong><JsonEvidence value={item} /></article>)}</div></EvidenceSection>
      {comparison.data.compatible && <EvidenceSection icon={ChartNoAxesCombined} title="Metric comparison" subtitle="Only compatible calculation versions"><div className="comparison-metrics">{comparison.data.metrics.map((metric) => <article key={`${metric.metric_group}:${metric.name}`}><strong>{metric.name}</strong><span>{metric.meaning}</span><JsonEvidence value={metric.values} /></article>)}</div></EvidenceSection>}
    </>}
  </div>;
}

export function DossierPage({
  initialExperimentId = "",
  initialMode = "dossier",
  initialComparisonIds = [],
}: {
  initialExperimentId?: string;
  initialMode?: "dossier" | "comparison";
  initialComparisonIds?: string[];
}) {
  const navigate = useNavigate();
  const mode = initialMode;
  const [input, setInput] = useState(initialExperimentId);
  const [inputError, setInputError] = useState("");
  const [action, setAction] = useState<{ status: "idle" | "loading" | "ready" | "failed"; error: string }>({ status: "idle", error: "" });
  const dossier = useQuery<ExperimentDossier>({
    queryKey: ["experiment-dossier", initialExperimentId],
    queryFn: ({ signal }) => fetchExperimentDossier(initialExperimentId, signal),
    enabled: Boolean(initialExperimentId),
    retry: false,
  });

  useEffect(() => setInput(initialExperimentId), [initialExperimentId]);

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = input.trim();
    if (!isExperimentId(value)) {
      setInputError("Experiment ID phải có dạng exp_v1_ theo sau bởi 32 ký tự hex.");
      return;
    }
    setInputError("");
    if (value === initialExperimentId) void dossier.refetch();
    else void navigate({ to: "/evidence/$experimentId", params: { experimentId: value } });
  };

  const runDownload = async (operation: () => Promise<DownloadPayload>) => {
    setAction({ status: "loading", error: "" });
    try {
      saveDownload(await operation());
      setAction({ status: "ready", error: "" });
    } catch (error) {
      setAction({ status: "failed", error: error instanceof ProductApiError ? error.message : "Download failed." });
    }
  };

  const state: EvidenceViewState = dossier.data
    ? "ready"
    : dossier.isFetching
      ? "loading"
      : dossier.error
        ? viewState(dossier.error)
        : "idle";
  const message = dossier.error instanceof Error ? dossier.error.message : undefined;
  const comparisonSeed = initialComparisonIds.length
    ? initialComparisonIds
    : initialExperimentId
      ? [initialExperimentId]
      : [];

  return (
    <Tabs.Root className="dossier-page" value={mode} onValueChange={(value) => {
      if (value === "comparison") void navigate({ to: "/evidence/compare", search: { experiments: comparisonSeed } });
      else void navigate({ to: "/evidence", search: { status: "all" } });
    }}>
      <div className="dossier-navigation">
        <Link to="/evidence" search={{ status: "all" }}><ArrowLeft size={14} /> Experiments</Link>
        {initialExperimentId && <button type="button" className="secondary-action" onClick={() => { void navigate({ to: "/evidence/compare", search: { experiments: [initialExperimentId] } }); }}><GitCompare size={14} /> So sánh experiment này</button>}
      </div>
      <Tabs.List className="mode-switch" aria-label="Evidence view">
        <Tabs.Trigger value="dossier" className={mode === "dossier" ? "active" : ""}><FileSearch size={15} /> Dossier</Tabs.Trigger>
        <Tabs.Trigger value="comparison" className={mode === "comparison" ? "active" : ""}><GitCompare size={15} /> Comparison</Tabs.Trigger>
      </Tabs.List>
      <Tabs.Content className="dossier-tab-panel" value="dossier">
        <form className="dossier-search" onSubmit={submit} noValidate><label><span>Experiment ID</span><input value={input} onChange={(event) => { setInput(event.target.value); setInputError(""); }} placeholder="exp_v1_…" autoComplete="off" /></label><button type="submit" className="primary-action"><FileSearch size={15} /> Inspect</button></form>
        {inputError && <p className="inline-error" role="alert">{inputError}</p>}
        {state !== "ready" && <EvidenceState state={state} {...(message ? { message } : {})} />}
        {action.status === "failed" && <p className="inline-error" role="alert">{action.error}</p>}
        {dossier.data && <DossierView dossier={dossier.data} exportState={action.status} onExport={() => runDownload(() => exportExperimentDossier(dossier.data.experiment.experiment_id))} onArtifact={(artifact) => artifact.artifact_id && runDownload(() => downloadArtifact(artifact.artifact_id!))} />}
      </Tabs.Content>
      <Tabs.Content className="dossier-tab-panel" value="comparison">
        <ComparisonView initialIds={initialComparisonIds} />
      </Tabs.Content>
    </Tabs.Root>
  );
}
