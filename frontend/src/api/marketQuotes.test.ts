import assert from "node:assert/strict";
import { onTestFinished, test } from "vitest";

import {
  MARKET_RANGES,
  ProductApiError,
  fetchMarketHistory,
  fetchMarketQuotes,
  isMarketRange,
  isMarketSymbol,
} from "./marketQuotes.js";

type Recorded = { url: string; options: RequestInit };

function stubFetch(handler: (url: string) => Response) {
  const originalFetch = globalThis.fetch;
  onTestFinished(() => {
    globalThis.fetch = originalFetch;
  });
  const requests: Recorded[] = [];
  globalThis.fetch = (async (input: string | URL | Request, options: RequestInit = {}) => {
    const url = String(input);
    requests.push({ url, options });
    return handler(url);
  }) as typeof fetch;
  return requests;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const QUOTE = {
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

const QUOTES_PAGE = { as_of: "2026-10-16T09:00:00+07:00", stale: false, quotes: [QUOTE] };

const HISTORY_PAGE = {
  as_of: "2026-10-16T09:00:00+07:00",
  stale: false,
  symbol: "VNM",
  range: "1mo",
  currency: "VND",
  quote: QUOTE,
  bars: [
    { time: "2026-09-15", close: 59_700, volume: 1_835_300 },
    { time: "2026-09-16", close: 59_400, volume: null },
  ],
};

test("the quote list is fetched from the same-origin product API with no symbols parameter", async () => {
  const requests = stubFetch(() => json(QUOTES_PAGE));

  const page = await fetchMarketQuotes();

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/v1/market/quotes");
  assert.equal(requests[0].url.includes("symbols"), false);
  assert.equal(requests[0].options.cache, "no-store");
  assert.equal(
    new Headers(requests[0].options.headers).get("accept"),
    "application/json",
  );
  assert.equal(page.quotes[0].price, 57_200);
  assert.equal(page.stale, false);
});

test("history carries the symbol and range as query parameters", async () => {
  const requests = stubFetch(() => json(HISTORY_PAGE));

  const page = await fetchMarketHistory("VNM", "3mo");

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/v1/market/history?symbol=VNM&range=3mo");
  assert.equal(page.bars.length, 2);
  assert.equal(page.bars[1].volume, null);
});

test("a contract violation is rejected with invalid_contract instead of being rendered", async () => {
  stubFetch(() => json({ as_of: "2026-10-16T09:00:00+07:00", stale: false, quotes: [{ symbol: "VNM" }] }));

  await assert.rejects(
    () => fetchMarketQuotes(),
    (error: unknown) => {
      assert.ok(error instanceof ProductApiError);
      assert.equal(error.code, "invalid_contract");
      assert.equal(error.status, 502);
      return true;
    },
  );
});

test("an unknown provider error code degrades to the row rather than failing the page", async () => {
  stubFetch(() => json({
    as_of: "2026-10-16T09:00:00+07:00",
    stale: true,
    quotes: [{ ...QUOTE, price: null, change_percent: null, error: "rate_limited" }],
  }));

  const page = await fetchMarketQuotes();

  assert.equal(page.quotes[0].error, "rate_limited");
  assert.equal(page.stale, true);
});

test("backend status codes reach the caller unchanged", async () => {
  stubFetch(() => json({ detail: "Không có dữ liệu cho mã này." }, 404));

  await assert.rejects(
    () => fetchMarketHistory("ZZZ", "1mo"),
    (error: unknown) => {
      assert.ok(error instanceof ProductApiError);
      assert.equal(error.status, 404);
      assert.equal(error.message, "Không có dữ liệu cho mã này.");
      return true;
    },
  );
});

test("an upstream outage surfaces as its own status, not as a parse error", async () => {
  stubFetch(() => json({ detail: "Nguồn dữ liệu đang lỗi." }, 502));

  await assert.rejects(
    () => fetchMarketQuotes(),
    (error: unknown) => {
      assert.ok(error instanceof ProductApiError);
      assert.equal(error.status, 502);
      assert.equal(error.message, "Nguồn dữ liệu đang lỗi.");
      return true;
    },
  );
});

test("a non-JSON success response is refused before parsing", async () => {
  stubFetch(() => new Response("<html>proxy</html>", {
    status: 200,
    headers: { "content-type": "text/html" },
  }));

  await assert.rejects(
    () => fetchMarketQuotes(),
    (error: unknown) => {
      assert.ok(error instanceof ProductApiError);
      assert.equal(error.code, "invalid_contract");
      return true;
    },
  );
});

test("an oversized response is refused instead of being parsed", async () => {
  stubFetch(() => new Response("x".repeat(4 * 1024 * 1024 + 1), {
    status: 200,
    headers: { "content-type": "application/json" },
  }));

  await assert.rejects(
    () => fetchMarketQuotes(),
    (error: unknown) => {
      assert.ok(error instanceof ProductApiError);
      assert.equal(error.code, "response_too_large");
      return true;
    },
  );
});

test("a bad symbol or range is rejected locally, without a round trip", async () => {
  const requests = stubFetch(() => json(HISTORY_PAGE));

  await assert.rejects(() => fetchMarketHistory("../../etc/passwd", "1mo"), ProductApiError);
  await assert.rejects(
    () => fetchMarketHistory("VNM", "5y" as unknown as (typeof MARKET_RANGES)[number]),
    ProductApiError,
  );
  assert.equal(requests.length, 0);
});

test("symbol and range guards match what the backend accepts", () => {
  assert.equal(isMarketSymbol("VNM"), true);
  assert.equal(isMarketSymbol("vn"), false);
  assert.equal(isMarketSymbol("VNM.VN"), false);
  assert.equal(isMarketSymbol("VNM1"), true);

  for (const range of MARKET_RANGES) assert.equal(isMarketRange(range), true);
  assert.equal(isMarketRange("1d"), false);
  assert.equal(isMarketRange("5y"), false);
});
