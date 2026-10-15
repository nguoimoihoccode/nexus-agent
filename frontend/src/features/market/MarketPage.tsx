import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChartNoAxesCombined, RefreshCw } from "lucide-react";

import {
  MARKET_RANGES,
  ProductApiError,
  type MarketQuote,
  type MarketRange,
  fetchMarketHistory,
  fetchMarketQuotes,
} from "../../api/marketQuotes.js";
import { AsyncState } from "../../components/AsyncState.js";
import { PriceChart } from "./PriceChart.js";
import {
  changeTone,
  formatChangePercent,
  formatMarketTime,
  formatVnd,
  formatVolume,
} from "./chartGeometry.js";

type MarketPageProps = {
  symbol: string;
  range: MarketRange;
  onSelect: (symbol: string) => void;
  onRangeChange: (range: MarketRange) => void;
};

const RANGE_LABELS: Record<MarketRange, string> = {
  "1mo": "1 tháng",
  "3mo": "3 tháng",
  "6mo": "6 tháng",
  "1y": "1 năm",
};

const ERROR_LABELS: Record<string, string> = {
  no_data: "Không có dữ liệu",
  timeout: "Nguồn dữ liệu không phản hồi",
  bad_payload: "Dữ liệu trả về không hợp lệ",
  upstream_error: "Nguồn dữ liệu đang lỗi",
};

function errorLabel(code: string | null): string {
  if (!code) return "—";
  return ERROR_LABELS[code] ?? "Không tải được mã này";
}

function message(error: unknown): string | undefined {
  return error instanceof Error && error.message ? error.message : undefined;
}

function stateOf(error: unknown): "unauthorized" | "empty" | "failed" {
  if (error instanceof ProductApiError && (error.status === 401 || error.status === 403)) return "unauthorized";
  if (error instanceof ProductApiError && error.status === 404) return "empty";
  return "failed";
}

function MarketState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const state = stateOf(error);
  const contents = {
    unauthorized: ["Không có quyền truy cập", "Tài khoản hiện tại không được phép xem dữ liệu thị trường."],
    empty: ["Không có dữ liệu", message(error) ?? "Nguồn dữ liệu không trả về mã này."],
    failed: ["Không tải được dữ liệu thị trường", message(error) ?? "Nguồn dữ liệu công khai đang không phản hồi."],
  } as const;
  const [title, detail] = contents[state];
  return (
    <AsyncState
      kind={state === "failed" ? "error" : state}
      title={title}
      message={detail}
      action={
        state === "unauthorized" ? undefined : (
          <button className="primary-action" type="button" onClick={onRetry}>
            Thử lại
          </button>
        )
      }
    />
  );
}

function rowLabel(quote: MarketQuote): string {
  const name = quote.name ?? quote.exchange ?? "";
  if (quote.error) {
    return [quote.symbol, name, errorLabel(quote.error)].filter(Boolean).join(", ");
  }
  // The row is a grid of bare spans, so without this a screen reader announces
  // "VNMVIETNAM DAIRY PRODUCTS JSC57.200-1.21%" as one run-on string.
  return [
    quote.symbol,
    name,
    `giá ${formatVnd(quote.price)} đồng`,
    `thay đổi ${formatChangePercent(quote.change_percent)}`,
    `khối lượng ${formatVolume(quote.volume)}`,
  ].filter(Boolean).join(", ");
}

function QuoteRow({ quote, selected, onSelect }: {
  quote: MarketQuote;
  selected: boolean;
  onSelect: () => void;
}) {
  const tone = changeTone(quote.change_percent);
  return (
    <button
      type="button"
      className={`market-row${selected ? " selected" : ""}`}
      aria-current={selected ? "true" : undefined}
      aria-label={rowLabel(quote)}
      onClick={onSelect}
    >
      <span className="market-symbol">{quote.symbol}</span>
      <span className="market-name">{quote.name ?? quote.exchange ?? "—"}</span>
      {quote.error ? (
        <span className="market-unavailable">{errorLabel(quote.error)}</span>
      ) : (
        <>
          <span className="market-price">{formatVnd(quote.price)}</span>
          <span className={`market-change ${tone}`}>{formatChangePercent(quote.change_percent)}</span>
        </>
      )}
      <span className="market-volume">{formatVolume(quote.volume)}</span>
    </button>
  );
}

function QuoteSummary({ quote }: { quote: MarketQuote }) {
  const tone = changeTone(quote.change_percent);
  return (
    <dl className="market-summary">
      <div>
        <dt>Giá</dt>
        <dd className="market-price">{formatVnd(quote.price)}</dd>
      </div>
      <div>
        <dt>Thay đổi</dt>
        <dd className={`market-change ${tone}`}>{formatChangePercent(quote.change_percent)}</dd>
      </div>
      <div>
        <dt>Khối lượng</dt>
        <dd>{formatVolume(quote.volume)}</dd>
      </div>
      <div>
        <dt>52 tuần</dt>
        <dd>
          {formatVnd(quote.fifty_two_week_low)} – {formatVnd(quote.fifty_two_week_high)}
        </dd>
      </div>
      <div>
        <dt>Cập nhật</dt>
        <dd>{formatMarketTime(quote.market_time)}</dd>
      </div>
    </dl>
  );
}

export function MarketPage({ symbol, range, onSelect, onRangeChange }: MarketPageProps) {
  const quotes = useQuery({
    queryKey: ["market-quotes"],
    queryFn: ({ signal }) => fetchMarketQuotes(signal),
    // Matches the backend cache TTL so a remount does not burn a provider call.
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
  const history = useQuery({
    queryKey: ["market-history", symbol, range],
    queryFn: ({ signal }) => fetchMarketHistory(symbol, range, signal),
    enabled: symbol !== "",
    staleTime: 60_000,
  });

  const refresh = () => {
    void quotes.refetch();
    if (symbol) void history.refetch();
  };

  let body: ReactNode;
  if (quotes.status === "pending") {
    body = (
      <AsyncState
        kind="loading"
        title="Đang tải bảng giá"
        message="Đang lấy dữ liệu từ nguồn công khai bên thứ ba."
      />
    );
  } else if (quotes.status === "error") {
    body = <MarketState error={quotes.error} onRetry={refresh} />;
  } else {
    const selectedQuote = quotes.data.quotes.find((quote) => quote.symbol === symbol);
    body = (
      <div className="market-layout">
        <ul className="market-list">
          {quotes.data.quotes.map((quote) => (
            <li key={quote.symbol}>
              <QuoteRow
                quote={quote}
                selected={quote.symbol === symbol}
                onSelect={() => onSelect(quote.symbol)}
              />
            </li>
          ))}
        </ul>

        <section className="market-detail" aria-label="Chi tiết mã">
          {symbol === "" ? (
            <AsyncState
              kind="empty"
              title="Chọn một mã để xem biểu đồ"
              message="Danh sách bên trái là các mã vốn hóa lớn trên HOSE."
            />
          ) : history.status === "pending" ? (
            <AsyncState kind="loading" title={`Đang tải ${symbol}`} message="Đang lấy lịch sử giá." />
          ) : history.status === "error" ? (
            <MarketState error={history.error} onRetry={refresh} />
          ) : (
            <>
              <header className="market-detail-head">
                <div>
                  <h3>
                    {symbol}
                    {selectedQuote?.name ? ` · ${selectedQuote.name}` : ""}
                  </h3>
                  <p className="market-detail-meta">
                    {selectedQuote?.exchange ?? "HOSE"} · {history.data.currency ?? "VND"} ·{" "}
                    {RANGE_LABELS[range]}
                  </p>
                </div>
                <div className="market-range" role="group" aria-label="Khoảng thời gian">
                  {MARKET_RANGES.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className={`market-range-option${option === range ? " selected" : ""}`}
                      aria-pressed={option === range}
                      onClick={() => onRangeChange(option)}
                    >
                      {RANGE_LABELS[option]}
                    </button>
                  ))}
                </div>
              </header>
              <QuoteSummary quote={history.data.quote} />
              <PriceChart
                symbol={symbol}
                rangeLabel={RANGE_LABELS[range]}
                bars={history.data.bars}
              />
            </>
          )}
        </section>
      </div>
    );
  }

  return (
    <section className="market-page">
      <header className="market-toolbar">
        <div className="market-toolbar-title">
          <ChartNoAxesCombined size={20} aria-hidden="true" />
          <h2>Thị trường</h2>
        </div>
        <p className="market-notice">
          Dữ liệu tham khảo từ nguồn công khai, có thể chậm và chưa điều chỉnh cổ tức. Không
          dùng cho quyết định đầu tư và không thuộc pipeline quant có quản trị.
        </p>
        <div className="market-toolbar-side">
          {quotes.status === "success" && (
            <span className="market-stamp">
              Cập nhật {formatMarketTime(quotes.data.as_of)}
              {quotes.data.stale ? " (bản lưu cũ)" : ""}
            </span>
          )}
          <button className="secondary-action" type="button" onClick={refresh} disabled={quotes.isFetching}>
            <RefreshCw size={16} className={quotes.isFetching ? "spin" : ""} aria-hidden="true" />
            Làm mới
          </button>
        </div>
      </header>
      {body}
    </section>
  );
}
