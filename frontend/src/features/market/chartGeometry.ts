import type { MarketBar } from "../../api/marketQuotes.js";

export const CHART_WIDTH = 720;
export const CHART_HEIGHT = 260;
export const CHART_PADDING = { top: 16, right: 68, bottom: 26, left: 12 } as const;

const VOLUME_BAND = 48;
const GUTTER = 10;

export const VOLUME_BOTTOM = CHART_HEIGHT - CHART_PADDING.bottom;
export const VOLUME_TOP = VOLUME_BOTTOM - VOLUME_BAND;
export const PRICE_BOTTOM = VOLUME_TOP - GUTTER;

const PLOT_LEFT = CHART_PADDING.left;
const PLOT_RIGHT = CHART_WIDTH - CHART_PADDING.right;
const PLOT_WIDTH = PLOT_RIGHT - PLOT_LEFT;

export type PriceDomain = { min: number; max: number };
export type VolumeBar = { x: number; y: number; width: number; height: number };
export type ChartTick = { value: number; y: number };
export type DateTick = { index: number; x: number; label: string };

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * Padded price window for the vertical scale.
 *
 * A flat series would divide by zero when scaling, so it gets a synthetic
 * window of its own instead of collapsing to a single line at y = NaN.
 */
export function priceDomain(closes: readonly number[]): PriceDomain {
  const finite = closes.filter((value) => Number.isFinite(value));
  if (finite.length === 0) return { min: 0, max: 1 };
  const low = Math.min(...finite);
  const high = Math.max(...finite);
  if (low === high) {
    const pad = Math.abs(low) * 0.01 || 1;
    return { min: low - pad, max: high + pad };
  }
  const pad = (high - low) * 0.04;
  return { min: low - pad, max: high + pad };
}

export function pointX(index: number, count: number): number {
  if (count <= 1) return PLOT_LEFT + PLOT_WIDTH / 2;
  const clamped = Math.min(Math.max(index, 0), count - 1);
  return PLOT_LEFT + (clamped / (count - 1)) * PLOT_WIDTH;
}

export function priceY(value: number, domain: PriceDomain): number {
  const span = domain.max - domain.min;
  const ratio = span === 0 ? 0.5 : (value - domain.min) / span;
  const height = PRICE_BOTTOM - CHART_PADDING.top;
  return CHART_PADDING.top + (1 - ratio) * height;
}

/**
 * The `points` attribute of the price polyline, or "" for an empty series.
 *
 * x is index-based rather than time-proportional on purpose: Vietnamese trading
 * weeks are interrupted by long holidays, and spacing bars by calendar date
 * draws those gaps as a broken line.
 */
export function pricePoints(bars: readonly MarketBar[], domain: PriceDomain): string {
  return bars
    .map((bar, index) => `${round(pointX(index, bars.length))},${round(priceY(bar.close, domain))}`)
    .join(" ");
}

export function maxVolume(bars: readonly MarketBar[]): number {
  return bars.reduce((peak, bar) => (
    bar.volume !== null && Number.isFinite(bar.volume) ? Math.max(peak, bar.volume) : peak
  ), 0);
}

export function volumeBars(bars: readonly MarketBar[], peak: number): VolumeBar[] {
  if (bars.length === 0 || peak <= 0) return [];
  const slot = PLOT_WIDTH / bars.length;
  // Leave a gap between bars so a dense series still reads as separate columns.
  const width = Math.max(slot * 0.6, 1);
  return bars.flatMap((bar, index) => {
    if (bar.volume === null || !Number.isFinite(bar.volume) || bar.volume <= 0) return [];
    const height = (bar.volume / peak) * VOLUME_BAND;
    return [{
      x: round(pointX(index, bars.length) - width / 2),
      y: round(VOLUME_BOTTOM - height),
      width: round(width),
      height: round(height),
    }];
  });
}

export function priceTicks(domain: PriceDomain, count = 4): ChartTick[] {
  const ticks: ChartTick[] = [];
  for (let index = 0; index < count; index += 1) {
    const value = domain.min + ((domain.max - domain.min) * index) / (count - 1);
    ticks.push({ value, y: round(priceY(value, domain)) });
  }
  return ticks.reverse();
}

export function dateTicks(bars: readonly MarketBar[], count = 4): DateTick[] {
  if (bars.length === 0) return [];
  const wanted = Math.min(count, bars.length);
  const seen = new Set<number>();
  const ticks: DateTick[] = [];
  for (let slot = 0; slot < wanted; slot += 1) {
    const index = wanted === 1 ? 0 : Math.round((slot * (bars.length - 1)) / (wanted - 1));
    const bar = bars[index];
    if (!bar || seen.has(index)) continue;
    seen.add(index);
    ticks.push({
      index,
      x: round(pointX(index, bars.length)),
      label: formatShortDate(bar.time),
    });
  }
  return ticks;
}

// One formatter for the process: the board renders 312 rows of three prices
// each, and building an Intl.NumberFormat per cell is measurably wasteful.
const VND = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 });
const COUNT = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 });

export function formatVnd(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return VND.format(value);
}

/** Vietnamese magnitude suffixes, hand-rolled so the output is ICU-stable. */
export function formatVolume(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)} tr`;
  if (value >= 1_000) return `${Math.round(value / 1_000)} ng`;
  return String(value);
}

/** Integer counts, grouped the same way prices are. */
export function formatCount(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return COUNT.format(value);
}

/** `2026-10-15` -> `15/10`. */
export function formatShortDate(value: string): string {
  const matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return matched ? `${matched[3]}/${matched[2]}` : value;
}

/** `2026-10-15` -> `15/10/2026`. */
export function formatLongDate(value: string): string {
  const matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return matched ? `${matched[3]}/${matched[2]}/${matched[1]}` : value;
}

/**
 * The backend already emits Indochina Time with an explicit offset, so this
 * only reshapes the string and never re-interprets it in the browser's zone.
 */
export function formatMarketTime(value: string | null): string {
  if (!value) return "—";
  const matched = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  return matched ? `${matched[3]}/${matched[2]} ${matched[4]}:${matched[5]}` : value;
}
