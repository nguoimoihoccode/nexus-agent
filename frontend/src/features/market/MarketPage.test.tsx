// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { MarketPage } from "./MarketPage.js";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const VNM = {
  symbol: "VNM",
  name: "VIETNAM DAIRY PRODUCTS JSC",
  exchange: "HOSE",
  currency: "VND",
  price: 57_200,
  change_percent: -1.209,
  volume: 1_988_733,
  fifty_two_week_high: 75_500,
  fifty_two_week_low: 54_600,
  market_time: "2026-10-15T14:45:18+07:00",
  error: null,
};

const FPT = { ...VNM, symbol: "FPT", name: "FPT CORP", price: 58_000, change_percent: 1.576 };
const BROKEN = {
  ...VNM,
  symbol: "BSR",
  name: null,
  price: null,
  change_percent: null,
  volume: null,
  market_time: null,
  error: "upstream_error",
};

const QUOTES = {
  as_of: "2026-10-16T09:00:00+07:00",
  stale: false,
  quotes: [VNM, FPT, BROKEN],
};

const BARS = [
  { time: "2026-09-15", close: 59_700, volume: 1_835_300 },
  { time: "2026-09-16", close: 59_400, volume: 1_502_100 },
  { time: "2026-09-17", close: 58_100, volume: 990_000 },
];

const HISTORY = {
  as_of: "2026-10-16T09:00:00+07:00",
  stale: false,
  symbol: "VNM",
  range: "1mo",
  currency: "VND",
  quote: VNM,
  bars: BARS,
};

type Routes = Partial<Record<"quotes" | "history", () => Response>>;

function stubApi(routes: Routes) {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });
  const calls: string[] = [];
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = String(input);
    calls.push(url);
    const handler = url.includes("/v1/market/history") ? routes.history : routes.quotes;
    if (!handler) throw new Error(`unexpected request: ${url}`);
    return handler();
  }) as typeof fetch;
  return calls;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderPage(
  props: Partial<{
    symbol: string;
    range: "1mo" | "3mo" | "6mo" | "1y";
    onSelect: (symbol: string) => void;
    onRangeChange: (range: "1mo" | "3mo" | "6mo" | "1y") => void;
  }> = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onSelect = props.onSelect ?? (() => {});
  const onRangeChange = props.onRangeChange ?? (() => {});
  const view = render(
    <QueryClientProvider client={client}>
      <MarketPage
        symbol={props.symbol ?? ""}
        range={props.range ?? "1mo"}
        onSelect={onSelect}
        onRangeChange={onRangeChange}
      />
    </QueryClientProvider>,
  );
  return { ...view, onSelect, onRangeChange };
}

function rowFor(symbol: string): HTMLElement {
  const row = screen.getByRole("button", { name: new RegExp(`^${symbol}\\b`) });
  return row;
}

test("the quote list renders every symbol with its price and change", async () => {
  stubApi({ quotes: () => json(QUOTES) });
  renderPage();

  expect(await screen.findByText("VNM")).toBeTruthy();
  expect(rowFor("VNM").textContent).toContain("57.200");
  expect(rowFor("VNM").textContent).toContain("-1.21%");
  expect(rowFor("FPT").textContent).toContain("+1.58%");
  expect(rowFor("VNM").querySelector(".market-change")?.className).toContain("down");
  expect(rowFor("FPT").querySelector(".market-change")?.className).toContain("up");
});

test("a symbol the provider could not serve degrades to a label inside its row", async () => {
  stubApi({ quotes: () => json(QUOTES) });
  renderPage();

  await screen.findByText("VNM");
  expect(rowFor("BSR").textContent).toContain("Nguồn dữ liệu đang lỗi");
  expect(rowFor("BSR").querySelector(".market-unavailable")).toBeTruthy();
});

test("the displayed timestamp carries the provider's own offset, and stale data says so", async () => {
  stubApi({ quotes: () => json({ ...QUOTES, stale: true }) });
  renderPage();

  expect(await screen.findByText(/Cập nhật 16\/10 09:00 \(bản lưu cũ\)/)).toBeTruthy();
});

test("selecting a row reports the symbol instead of navigating itself", async () => {
  stubApi({ quotes: () => json(QUOTES) });
  const selected: string[] = [];
  renderPage({ onSelect: (symbol) => selected.push(symbol) });

  fireEvent.click(await screen.findByRole("button", { name: /^FPT\b/ }));

  expect(selected).toEqual(["FPT"]);
});

test("with nothing selected the detail pane asks for a choice and fetches no history", async () => {
  const calls = stubApi({ quotes: () => json(QUOTES) });
  renderPage();

  expect(await screen.findByText("Chọn một mã để xem biểu đồ")).toBeTruthy();
  expect(calls.some((url) => url.includes("/v1/market/history"))).toBe(false);
});

test("a selected symbol fetches its history and draws the chart with a text alternative", async () => {
  stubApi({ quotes: () => json(QUOTES), history: () => json(HISTORY) });
  renderPage({ symbol: "VNM" });

  const chart = await screen.findByRole("img", { name: /Biểu đồ giá đóng cửa VNM/ });
  expect(chart.getAttribute("aria-label")).toContain("3 phiên");
  expect(chart.querySelector(".market-chart-line")?.getAttribute("points")?.split(" ").length).toBe(3);
  expect(screen.getByText("Bảng giá đóng cửa (3 phiên)")).toBeTruthy();
  expect(screen.getByRole("rowheader", { name: "17/09/2026" })).toBeTruthy();
  expect(screen.getByText("VNM · VIETNAM DAIRY PRODUCTS JSC")).toBeTruthy();
});

test("changing the range reports the new range and refetches that range", async () => {
  const calls = stubApi({ quotes: () => json(QUOTES), history: () => json(HISTORY) });
  const ranges: string[] = [];
  renderPage({ symbol: "VNM", onRangeChange: (range) => ranges.push(range) });

  fireEvent.click(await screen.findByRole("button", { name: "3 tháng" }));

  expect(ranges).toEqual(["3mo"]);
  expect(calls.filter((url) => url.includes("range=1mo")).length).toBe(1);
});

test("the current range is marked for assistive technology", async () => {
  stubApi({ quotes: () => json(QUOTES), history: () => json(HISTORY) });
  renderPage({ symbol: "VNM", range: "6mo" });

  const option = await screen.findByRole("button", { name: "6 tháng" });
  expect(option.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "1 tháng" }).getAttribute("aria-pressed")).toBe("false");
});

test("history with no sessions explains itself instead of drawing an empty chart", async () => {
  stubApi({
    quotes: () => json(QUOTES),
    history: () => json({ ...HISTORY, bars: [] }),
  });
  renderPage({ symbol: "VNM" });

  expect(await screen.findByText(/Không có phiên giao dịch nào/)).toBeTruthy();
  expect(screen.queryByRole("img")).toBeNull();
});

test("an unauthorized account is told so and is not offered a pointless retry", async () => {
  stubApi({ quotes: () => json({ detail: "Forbidden" }, 403) });
  renderPage();

  expect(await screen.findByText("Không có quyền truy cập")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Thử lại" })).toBeNull();
});

test("a provider outage renders a retry that actually refetches", async () => {
  let attempt = 0;
  const calls = stubApi({
    quotes: () => {
      attempt += 1;
      return attempt === 1 ? json({ detail: "Nguồn dữ liệu đang lỗi." }, 502) : json(QUOTES);
    },
  });
  renderPage();

  expect(await screen.findByText("Không tải được dữ liệu thị trường")).toBeTruthy();
  expect(screen.getByText("Nguồn dữ liệu đang lỗi.")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Thử lại" }));

  await waitFor(() => expect(screen.queryByText("Không tải được dữ liệu thị trường")).toBeNull());
  expect(screen.getByText("VNM")).toBeTruthy();
  expect(calls.filter((url) => url.includes("/v1/market/quotes")).length).toBe(2);
});
