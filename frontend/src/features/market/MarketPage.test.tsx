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
  name: "CTCP Sữa Việt Nam",
  exchange: "HOSE",
  ref_price: 57_200,
  ceiling: 61_200,
  floor: 53_200,
  foreign_room: 1_234_567,
  trading_unit: 10,
};

const FPT = { ...VNM, symbol: "FPT", name: "CTCP FPT", ref_price: 58_000 };
const AAA = {
  ...VNM,
  symbol: "AAA",
  name: "CTCP Nhựa An Phát Xanh",
  ref_price: 7_000,
  ceiling: 7_490,
  floor: 6_510,
};
const MWG = { ...VNM, symbol: "MWG", name: "CTCP Đầu tư Thế Giới Di Động" };
// A listing SSI has not published a band for yet. The row stays, with dashes.
const NO_BAND = { ...VNM, symbol: "BSR", name: null, ref_price: null, ceiling: null, floor: null };

const QUOTES = {
  as_of: "2026-10-16T09:00:00+07:00",
  stale: false,
  trading_date: "2026-10-16",
  quotes: [VNM, FPT, AAA, MWG, NO_BAND],
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
  return screen.getByRole("button", { name: new RegExp(`^${symbol}\\b`) });
}

test("the board lists every symbol with its reference price, ceiling and floor", async () => {
  stubApi({ quotes: () => json(QUOTES) });
  renderPage();

  expect(await screen.findByText("VNM")).toBeTruthy();
  expect(rowFor("VNM").textContent).toContain("57.200");
  expect(rowFor("VNM").textContent).toContain("61.200");
  expect(rowFor("VNM").textContent).toContain("53.200");
  expect(screen.getByText("Tham chiếu")).toBeTruthy();
  expect(screen.getByText("5 mã")).toBeTruthy();
});

test("a row is announced field by field, because a bare number could be any of the three", async () => {
  stubApi({ quotes: () => json(QUOTES) });
  renderPage();

  const row = await screen.findByRole("button", { name: /^AAA\b/ });

  expect(row.getAttribute("aria-label")).toBe(
    "AAA, CTCP Nhựa An Phát Xanh, giá tham chiếu 7.000 đồng, giá trần 7.490 đồng, giá sàn 6.510 đồng",
  );
});

test("a listing with no published band keeps its row and shows dashes", async () => {
  stubApi({ quotes: () => json(QUOTES) });
  renderPage();

  const row = await screen.findByRole("button", { name: /^BSR\b/ });

  const cells = Array.from(row.querySelectorAll(".market-price, .market-band"));
  expect(cells.map((cell) => cell.textContent)).toEqual(["—", "—", "—"]);
});

test("the filter narrows the board by symbol, ignoring case", async () => {
  stubApi({ quotes: () => json(QUOTES) });
  renderPage();

  fireEvent.change(await screen.findByLabelText("Lọc theo mã hoặc tên"), {
    target: { value: "fpt" },
  });

  expect(rowFor("FPT")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^VNM\b/ })).toBeNull();
  expect(screen.getByText("1 / 5 mã")).toBeTruthy();
});

test("the filter matches company names typed without Vietnamese diacritics", async () => {
  stubApi({ quotes: () => json(QUOTES) });
  renderPage();

  const input = await screen.findByLabelText("Lọc theo mã hoặc tên");
  // "Nhựa An Phát Xanh" and "Đầu tư Thế Giới Di Động" typed on a plain keyboard.
  // `đ` has no NFD decomposition, so that fold is hand-rolled.
  for (const [typed, symbol] of [["nhua an phat", "AAA"], ["dau tu the gioi", "MWG"]]) {
    fireEvent.change(input, { target: { value: typed } });
    expect(rowFor(symbol)).toBeTruthy();
    expect(screen.getByText("1 / 5 mã")).toBeTruthy();
  }
});

test("a filter that matches nothing says so instead of rendering an empty list", async () => {
  stubApi({ quotes: () => json(QUOTES) });
  renderPage();

  fireEvent.change(await screen.findByLabelText("Lọc theo mã hoặc tên"), {
    target: { value: "zzzz" },
  });

  expect(screen.getByText("Không mã nào khớp từ khoá lọc.")).toBeTruthy();
  expect(screen.getByText("0 / 5 mã")).toBeTruthy();
});

test("the stamp names the session and marks a cached board as stale", async () => {
  stubApi({ quotes: () => json({ ...QUOTES, stale: true }) });
  renderPage();

  expect(
    await screen.findByText(/Phiên 16\/10\/2026 · cập nhật 16\/10 09:00 \(bản lưu cũ\)/),
  ).toBeTruthy();
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
  expect(screen.getByText("VNM · CTCP Sữa Việt Nam")).toBeTruthy();
});

test("the detail summary names every figure, and calls the base price a reference", async () => {
  stubApi({ quotes: () => json(QUOTES), history: () => json(HISTORY) });
  renderPage({ symbol: "VNM" });

  await screen.findByRole("img", { name: /Biểu đồ/ });

  expect(screen.getAllByRole("term").map((node) => node.textContent)).toEqual([
    "Tham chiếu",
    "Trần",
    "Sàn",
    "Room ngoại",
    "Đơn vị giao dịch",
    "Phiên",
  ]);
  expect(screen.getByText(/không phải giá khớp lệnh trong phiên/)).toBeTruthy();
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
