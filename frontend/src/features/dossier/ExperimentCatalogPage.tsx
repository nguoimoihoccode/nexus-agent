import { useMemo, useState, type FormEvent } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowRight,
  FileSearch,
  GitCompare,
  LoaderCircle,
  MessageSquare,
  RefreshCw,
  ShieldAlert,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import {
  ProductApiError,
  type ExperimentCatalogItem,
  type ExperimentStatus,
  fetchExperimentCatalog,
  isExperimentId,
} from "../../api/governedQuant.js";

export type ExperimentCatalogFilter = "all" | "active" | "completed" | "failed";

const CATALOG_STATUSES: Record<ExperimentCatalogFilter, ExperimentStatus[]> = {
  all: [],
  active: ["queued", "leased", "running", "cancelling"],
  completed: ["completed"],
  failed: ["failed", "cancelled", "expired"],
};
const CATALOG_STATUS_VALUES = [
  ...CATALOG_STATUSES.active,
  "completed",
  "failed",
  "cancelled",
  "expired",
];

function display(value: unknown, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function stateClass(value: unknown) {
  return typeof value === "string" && CATALOG_STATUS_VALUES.includes(value) ? value : "unknown";
}

function CatalogState({ state, message, onRetry }: {
  state: "loading" | "empty" | "unauthorized" | "failed";
  message?: string;
  onRetry?: () => void;
}) {
  const contents = {
    loading: [LoaderCircle, "Đang tải experiments", "Đang đọc lịch sử experiment của tài khoản hiện tại."],
    empty: [FileSearch, "Chưa có experiment", "Bắt đầu một quant research run trong Chat để tạo experiment đầu tiên."],
    unauthorized: [
      ShieldAlert,
      "Không có quyền truy cập",
      message || "Tài khoản hiện tại không có quyền đọc quant experiments.",
    ],
    failed: [
      TriangleAlert,
      "Không thể tải experiments",
      message || "Product API trả về lỗi khi đọc experiment catalog.",
    ],
  } satisfies Record<typeof state, readonly [LucideIcon, string, string]>;
  const [Icon, title, detail] = contents[state];

  return (
    <section className={`evidence-state ${state}`} role={state === "failed" ? "alert" : "status"}>
      <Icon className={state === "loading" ? "spin" : ""} size={28} />
      <strong>{title}</strong>
      <p>{detail}</p>
      {state === "empty" && (
        <Link className="secondary-action" to="/chat"><MessageSquare size={14} /> Mở Chat</Link>
      )}
      {state === "failed" && onRetry && (
        <button type="button" className="secondary-action" onClick={onRetry}>
          <RefreshCw size={14} /> Thử lại
        </button>
      )}
    </section>
  );
}

function ExperimentRow({ item, selected, selectionDisabled, onSelect }: {
  item: ExperimentCatalogItem;
  selected: boolean;
  selectionDisabled: boolean;
  onSelect: (selected: boolean) => void;
}) {
  const summary = item.specification_summary;

  return (
    <article className="experiment-row">
      <label className="experiment-select">
        <input
          type="checkbox"
          checked={selected}
          disabled={selectionDisabled}
          onChange={(event) => onSelect(event.target.checked)}
          aria-label={`Chọn experiment ${item.experiment_id}`}
        />
      </label>
      <div className="experiment-main">
        <div className="experiment-title">
          <Link to="/evidence/$experimentId" params={{ experimentId: item.experiment_id }}>
            {item.experiment_id}
          </Link>
          <span className={`state-badge ${stateClass(item.status)}`}>{item.status}</span>
        </div>
        <div className="experiment-summary">
          <span><small>Model</small>{display(summary.model)}</span>
          <span><small>Universe</small>{display(summary.universe)}</span>
          <span><small>Feature</small>{display(summary.feature_set)}</span>
          <span>
            <small>Test</small>
            {summary.test_start && summary.test_end
              ? `${summary.test_start} → ${summary.test_end}`
              : "—"}
          </span>
        </div>
        <div className="experiment-meta">
          <code>{item.dataset_revision_id}</code>
          <span>{new Date(item.created_at).toLocaleString("vi-VN")}</span>
        </div>
      </div>
      <Link
        className="experiment-open"
        to="/evidence/$experimentId"
        params={{ experimentId: item.experiment_id }}
        aria-label={`Mở experiment ${item.experiment_id}`}
      ><ArrowRight size={16} /></Link>
    </article>
  );
}

export function ExperimentCatalogPage({ filter }: { filter: ExperimentCatalogFilter }) {
  const navigate = useNavigate();
  const [experimentInput, setExperimentInput] = useState("");
  const [inputError, setInputError] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const catalog = useInfiniteQuery({
    queryKey: ["experiment-catalog", filter],
    queryFn: ({ pageParam, signal }) => fetchExperimentCatalog({
      limit: 20,
      statuses: CATALOG_STATUSES[filter],
      cursor: pageParam,
      signal,
    }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const experiments = useMemo(
    () => catalog.data?.pages.flatMap((page) => page.items) ?? [],
    [catalog.data],
  );

  const openExperiment = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = experimentInput.trim();
    if (!isExperimentId(value)) {
      setInputError("Experiment ID phải có dạng exp_v1_ theo sau bởi 32 ký tự hex.");
      return;
    }
    setInputError("");
    void navigate({ to: "/evidence/$experimentId", params: { experimentId: value } });
  };
  const selectExperiment = (experimentId: string, selected: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (selected && next.size < 5) next.add(experimentId);
      if (!selected) next.delete(experimentId);
      return next;
    });
  };
  const unauthorized = catalog.error instanceof ProductApiError
    && (catalog.error.status === 401 || catalog.error.status === 403);
  const errorMessage = catalog.error instanceof Error ? catalog.error.message : undefined;

  return (
    <div className="experiments-page">
      <section className="experiments-intro">
        <div>
          <span className="eyebrow">Actor-scoped quant history</span>
          <h2>Experiment history</h2>
          <p>Mở lại dossier, kiểm tra evidence hoặc chọn nhiều experiment để so sánh.</p>
        </div>
        <form className="experiment-id-search" onSubmit={openExperiment} noValidate>
          <label>
            <span>Open by experiment ID</span>
            <input
              value={experimentInput}
              onChange={(event) => {
                setExperimentInput(event.target.value);
                setInputError("");
              }}
              placeholder="exp_v1_…"
              autoComplete="off"
            />
          </label>
          <button type="submit" className="secondary-action"><FileSearch size={14} /> Mở</button>
        </form>
      </section>
      {inputError && <p className="inline-error" role="alert">{inputError}</p>}
      <nav className="experiment-filters" aria-label="Lọc experiments">
        {(["all", "active", "completed", "failed"] as const).map((value) => (
          <button
            key={value}
            type="button"
            className={filter === value ? "active" : ""}
            onClick={() => {
              setSelectedIds(new Set());
              void navigate({ to: "/evidence", search: { status: value }, replace: true });
            }}
          >
            {value === "all" ? "Tất cả" : value === "active" ? "Đang chạy"
              : value === "completed" ? "Hoàn tất" : "Không thành công"}
          </button>
        ))}
      </nav>
      {catalog.isPending && <CatalogState state="loading" />}
      {!catalog.isPending && unauthorized && (
        <CatalogState state="unauthorized" {...(errorMessage ? { message: errorMessage } : {})} />
      )}
      {!catalog.isPending && catalog.error && !unauthorized && (
        <CatalogState
          state="failed"
          {...(errorMessage ? { message: errorMessage } : {})}
          onRetry={() => { void catalog.refetch(); }}
        />
      )}
      {!catalog.isPending && !catalog.error && experiments.length === 0 && (
        <CatalogState state="empty" />
      )}
      {experiments.length > 0 && (
        <>
          <section className="experiment-selection" aria-live="polite">
            <span>{selectedIds.size} / 5 đã chọn</span>
            <button
              type="button"
              className="primary-action"
              disabled={selectedIds.size < 2}
              onClick={() => {
                void navigate({
                  to: "/evidence/compare",
                  search: { experiments: [...selectedIds] },
                });
              }}
            ><GitCompare size={14} /> So sánh</button>
          </section>
          <div className="experiment-list">
            {experiments.map((item) => (
              <ExperimentRow
                key={item.experiment_id}
                item={item}
                selected={selectedIds.has(item.experiment_id)}
                selectionDisabled={selectedIds.size >= 5 && !selectedIds.has(item.experiment_id)}
                onSelect={(selected) => selectExperiment(item.experiment_id, selected)}
              />
            ))}
          </div>
          {catalog.hasNextPage && (
            <button
              type="button"
              className="load-more-experiments"
              disabled={catalog.isFetchingNextPage}
              onClick={() => { void catalog.fetchNextPage(); }}
            >{catalog.isFetchingNextPage ? "Đang tải…" : "Load more"}</button>
          )}
        </>
      )}
    </div>
  );
}
