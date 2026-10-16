import { useMemo, useState, type ReactNode } from "react";
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
  formatCount,
  formatLongDate,
  formatMarketTime,
  formatVnd,
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

const COMBINING_MARKS = /[̀-ͯ]/g;

/**
 * Case- and accent-insensitive key for the filter box.
 *
 * The board carries Vietnamese company names, so "nhua an phat" has to find
 * "Nhựa An Phát Xanh". NFD splits most diacritics into a combining mark, but
 * `đ` has no decomposition, so it is folded by hand.
 */
function fold(value: string): string {
  return value.normalize("NFD").replace(COMBINING_MARKS, "").toLowerCase().replace(/đ/g, "d");
}

function matches(quote: MarketQuote, needle: string): boolean {
  if (needle === "") return true;
  return fold(quote.symbol).includes(needle) || fold(quote.name ?? "").includes(needle);
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
  // The row is a grid of bare spans, so without this a screen reader announces
  // "AAAAN PHAT BIOPLASTICS7.0007.4906.510" as one run-on string. Every value is
  // labelled with what it is, because a bare number here is a reference price,
  // a ceiling, or a floor and they are not interchangeable.
  return [
    quote.symbol,
    name,
    `giá tham chiếu ${formatVnd(quote.ref_price)} đồng`,
    `giá trần ${formatVnd(quote.ceiling)} đồng`,
    `giá sàn ${formatVnd(quote.floor)} đồng`,
  ].filter(Boolean).join(", ");
}

function QuoteRow({ quote, selected, onSelect }: {
  quote: MarketQuote;
  selected: boolean;
  onSelect: () => void;
}) {
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
      <span className="market-price">{formatVnd(quote.ref_price)}</span>
      <span className="market-band">{formatVnd(quote.ceiling)}</span>
      <span className="market-band">{formatVnd(quote.floor)}</span>
    </button>
  );
}

function QuoteSummary({ quote, tradingDate }: {
  quote: MarketQuote;
  tradingDate: string | null;
}) {
  return (
    <dl className="market-summary">
      <div>
        <dt>Tham chiếu</dt>
        <dd className="market-price">{formatVnd(quote.ref_price)}</dd>
      </div>
      <div>
        <dt>Trần</dt>
        <dd>{formatVnd(quote.ceiling)}</dd>
      </div>
      <div>
        <dt>Sàn</dt>
        <dd>{formatVnd(quote.floor)}</dd>
      </div>
      <div>
        <dt>Room ngoại</dt>
        <dd>{formatCount(quote.foreign_room)}</dd>
      </div>
      <div>
        <dt>Đơn vị giao dịch</dt>
        <dd>{formatCount(quote.trading_unit)}</dd>
      </div>
      <div>
        <dt>Phiên</dt>
        <dd>{tradingDate ? formatLongDate(tradingDate) : "—"}</dd>
      </div>
    </dl>
  );
}

export function MarketPage({ symbol, range, onSelect, onRangeChange }: MarketPageProps) {
  const [query, setQuery] = useState("");
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

  const rows = quotes.data?.quotes;
  const needle = fold(query.trim());
  const visible = useMemo(
    () => (rows ?? []).filter((quote) => matches(quote, needle)),
    [rows, needle],
  );

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
        <div className="market-board">
          <div className="market-filter">
            <label htmlFor="market-filter-input">Lọc theo mã hoặc tên</label>
            <input
              id="market-filter-input"
              type="search"
              value={query}
              placeholder="Ví dụ: FPT hoặc nhựa an phát"
              onChange={(event) => setQuery(event.target.value)}
            />
            <p className="market-filter-count" role="status">
              {needle === ""
                ? `${quotes.data.quotes.length} mã`
                : `${visible.length} / ${quotes.data.quotes.length} mã`}
            </p>
          </div>

          <ul className="market-list">
            {/* Column alignment only. Each row carries a full aria-label, so
                hiding this from assistive technology loses no information. */}
            <li className="market-row market-list-head" aria-hidden="true">
              <span>Mã</span>
              <span>Tên</span>
              <span>Tham chiếu</span>
              <span>Trần</span>
              <span>Sàn</span>
            </li>
            {visible.map((quote) => (
              <li key={quote.symbol}>
                <QuoteRow
                  quote={quote}
                  selected={quote.symbol === symbol}
                  onSelect={() => onSelect(quote.symbol)}
                />
              </li>
            ))}
            {visible.length === 0 && (
              <li className="market-empty-note">Không mã nào khớp từ khoá lọc.</li>
            )}
          </ul>
        </div>

        <section className="market-detail" aria-label="Chi tiết mã">
          {symbol === "" ? (
            <AsyncState
              kind="empty"
              title="Chọn một mã để xem biểu đồ"
              message="Danh sách bên trái là các mã cổ phiếu niêm yết trên HOSE."
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
              {selectedQuote && (
                <QuoteSummary
                  quote={selectedQuote}
                  tradingDate={quotes.data.trading_date}
                />
              )}
              <p className="market-reference-note">
                Tham chiếu là mức giá cơ sở do sàn công bố cho phiên, dùng để tính giá trần
                và giá sàn — không phải giá khớp lệnh trong phiên.
              </p>
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
          Bảng giá HOSE theo dữ liệu công khai của SSI: mỗi mã chỉ có giá tham chiếu, giá
          trần và giá sàn, không có giá khớp lệnh. Biểu đồ là giá đóng cửa của phiên đã
          đóng gần nhất. Không dùng cho quyết định đầu tư và không thuộc pipeline quant có
          quản trị.
        </p>
        <div className="market-toolbar-side">
          {quotes.status === "success" && (
            <span className="market-stamp">
              {quotes.data.trading_date
                ? `Phiên ${formatLongDate(quotes.data.trading_date)} · `
                : ""}
              cập nhật {formatMarketTime(quotes.data.as_of)}
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
