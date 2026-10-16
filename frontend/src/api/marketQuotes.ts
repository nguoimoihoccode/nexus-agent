import { z } from "zod";

import { authenticatedHeaders, notifyAuthExpired } from "../auth/session.js";
import { ProductApiError } from "./governedQuant.js";

// The shared product-API error, re-exported so consumers of the market client
// do not have to reach into the dossier module for it.
export { ProductApiError } from "./governedQuant.js";

const API_URL = import.meta.env.VITE_LANGGRAPH_API_URL || "/api";
const MAX_PRODUCT_JSON_BYTES = 4 * 1024 * 1024;
const MAX_ERROR_BYTES = 64 * 1024;
const SYMBOL_PATTERN = /^[A-Z0-9]{3,10}$/;
const BAR_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export const MARKET_RANGES = ["1mo", "3mo", "6mo", "1y"] as const;
export type MarketRange = (typeof MARKET_RANGES)[number];

const rangeSchema = z.enum(MARKET_RANGES);
const requiredText = z.string().min(1).max(512);
const optionalText = z.string().max(512).nullable();
const priceSchema = z.number().nonnegative().nullable();

// The provider quotes a reference price for the session, not a matched trade
// price, so there is no change, volume, or intraday timestamp to carry. Both
// the band and the symbol count are the provider's: the board is one listing
// per HOSE stock, and the range is wide enough that a growing market does not
// turn the whole page into an invalid_contract error.
const quoteSchema = z.object({
  symbol: z.string().regex(SYMBOL_PATTERN),
  name: optionalText,
  exchange: optionalText,
  ref_price: priceSchema,
  ceiling: priceSchema,
  floor: priceSchema,
  foreign_room: z.number().int().nonnegative().nullable(),
  trading_unit: z.number().int().positive().nullable(),
});

const barSchema = z.object({
  time: z.string().regex(BAR_DATE_PATTERN),
  close: z.number().nonnegative(),
  volume: z.number().int().nonnegative().nullable(),
});

const quotesPageSchema = z.object({
  as_of: requiredText,
  stale: z.boolean(),
  // The session the reference prices belong to. Nullable because a board row
  // without a session date is still a usable row.
  trading_date: z.string().regex(BAR_DATE_PATTERN).nullable(),
  quotes: z.array(quoteSchema).max(512),
});

const historyPageSchema = z.object({
  as_of: requiredText,
  stale: z.boolean(),
  symbol: z.string().regex(SYMBOL_PATTERN),
  range: rangeSchema,
  currency: optionalText,
  quote: quoteSchema,
  bars: z.array(barSchema).max(400),
});

export type MarketQuote = z.infer<typeof quoteSchema>;
export type MarketBar = z.infer<typeof barSchema>;
export type MarketQuotePage = z.infer<typeof quotesPageSchema>;
export type MarketHistoryPage = z.infer<typeof historyPageSchema>;

export function isMarketSymbol(value: string): boolean {
  return SYMBOL_PATTERN.test(value);
}

export function isMarketRange(value: string): value is MarketRange {
  return (MARKET_RANGES as readonly string[]).includes(value);
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

async function request(path: string, signal?: AbortSignal): Promise<Response> {
  const init: RequestInit = {
    headers: await authenticatedHeaders({ Accept: "application/json" }),
    cache: "no-store",
  };
  if (signal) init.signal = signal;
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

export async function fetchMarketQuotes(signal?: AbortSignal): Promise<MarketQuotePage> {
  // No `symbols` parameter: the board is whatever the provider lists as HOSE,
  // so there is only one place for the universe to change.
  return parseContract(
    quotesPageSchema,
    await readJsonResponse(await request("/v1/market/quotes", signal)),
    "Unsupported market quote contract.",
  );
}

export async function fetchMarketHistory(
  symbol: string,
  range: MarketRange,
  signal?: AbortSignal,
): Promise<MarketHistoryPage> {
  if (!isMarketSymbol(symbol) || !isMarketRange(range)) {
    throw new ProductApiError("Mã hoặc khoảng thời gian không hợp lệ.", 422, "invalid_id");
  }
  const query = new URLSearchParams({ symbol, range });
  return parseContract(
    historyPageSchema,
    await readJsonResponse(await request(`/v1/market/history?${query}`, signal)),
    "Unsupported market history contract.",
  );
}
