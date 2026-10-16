import assert from "node:assert/strict";
import { test } from "vitest";

import {
  CHART_PADDING,
  CHART_WIDTH,
  PRICE_BOTTOM,
  VOLUME_TOP,
  dateTicks,
  formatCount,
  formatLongDate,
  formatMarketTime,
  formatShortDate,
  formatVnd,
  formatVolume,
  maxVolume,
  priceDomain,
  pricePoints,
  priceTicks,
  priceY,
  volumeBars,
} from "./chartGeometry.js";

const bar = (time: string, close: number, volume: number | null = 1000) => ({ time, close, volume });

function series(count: number) {
  return Array.from({ length: count }, (_, index) => (
    bar(`2026-01-${String((index % 28) + 1).padStart(2, "0")}`, 10_000 + index * 137, 1_000 + index)
  ));
}

test("an empty series produces no points and a usable domain", () => {
  assert.equal(pricePoints([], priceDomain([])), "");
  const domain = priceDomain([]);
  assert.ok(Number.isFinite(domain.min) && Number.isFinite(domain.max));
  assert.ok(domain.max > domain.min);
});

test("a single bar lands inside the plot instead of dividing by zero", () => {
  const bars = [bar("2026-10-15", 50_000)];
  const points = pricePoints(bars, priceDomain(bars.map((item) => item.close)));
  assert.equal(points.split(" ").length, 1);
  const [x, y] = points.split(",").map(Number);
  assert.ok(Number.isFinite(x) && Number.isFinite(y));
  assert.ok(y >= CHART_PADDING.top && y <= PRICE_BOTTOM);
});

test("a flat series never yields NaN", () => {
  const bars = Array.from({ length: 12 }, (_, index) => bar(`2026-02-${String(index + 1).padStart(2, "0")}`, 30_000));
  const domain = priceDomain(bars.map((item) => item.close));
  assert.ok(domain.max > domain.min);

  const points = pricePoints(bars, domain);
  assert.equal(points.includes("NaN"), false);
  for (const pair of points.split(" ")) {
    const [x, y] = pair.split(",").map(Number);
    assert.ok(Number.isFinite(x) && Number.isFinite(y), pair);
  }
});

test("every point of a long series stays inside the plot area", () => {
  const bars = series(250);
  const points = pricePoints(bars, priceDomain(bars.map((item) => item.close)));
  const pairs = points.split(" ");
  assert.equal(pairs.length, 250);
  for (const pair of pairs) {
    const [x, y] = pair.split(",").map(Number);
    assert.ok(x >= CHART_PADDING.left && x <= CHART_WIDTH - CHART_PADDING.right, `x ${x}`);
    assert.ok(y >= CHART_PADDING.top && y <= PRICE_BOTTOM, `y ${y}`);
  }
});

test("a higher price maps to a smaller y, and the line clears the volume band", () => {
  const domain = priceDomain([20_000, 30_000]);
  assert.ok(priceY(30_000, domain) < priceY(20_000, domain));
  assert.ok(priceY(domain.min, domain) <= PRICE_BOTTOM);
  assert.ok(PRICE_BOTTOM < VOLUME_TOP);
});

test("missing volume omits its bar rather than drawing a zero-height one", () => {
  const bars = [bar("2026-10-13", 100, 500), bar("2026-10-14", 101, null), bar("2026-10-15", 102, 0)];
  const drawn = volumeBars(bars, maxVolume(bars));
  assert.equal(drawn.length, 1);
  assert.equal(drawn[0].height > 0, true);
});

test("a series with no volume at all draws no volume bars", () => {
  const bars = [bar("2026-10-14", 100, null), bar("2026-10-15", 101, null)];
  assert.equal(maxVolume(bars), 0);
  assert.deepEqual(volumeBars(bars, maxVolume(bars)), []);
});

test("price ticks span the domain from top to bottom", () => {
  const domain = priceDomain([10_000, 20_000]);
  const ticks = priceTicks(domain, 4);
  assert.equal(ticks.length, 4);
  assert.equal(ticks[0].y <= ticks[3].y, true);
  for (const tick of ticks) assert.ok(Number.isFinite(tick.y));
});

test("date ticks are ordered, unique and labelled as day/month", () => {
  const ticks = dateTicks(series(30), 4);
  assert.equal(ticks.length, 4);
  assert.deepEqual([...ticks].map((tick) => tick.index).sort((a, b) => a - b), ticks.map((tick) => tick.index));
  assert.equal(new Set(ticks.map((tick) => tick.index)).size, 4);
  for (const tick of ticks) assert.match(tick.label, /^\d{2}\/\d{2}$/);
});

test("date ticks handle a short series without repeating", () => {
  assert.deepEqual(dateTicks([], 4), []);
  const single = dateTicks([bar("2026-10-15", 1)], 4);
  assert.equal(single.length, 1);
  const two = dateTicks(series(2), 4);
  assert.equal(two.length, 2);
});

test("values are formatted with Vietnamese grouping", () => {
  assert.equal(formatVnd(null), "—");
  assert.match(formatVnd(57_200), /^57[.,\s]?200$/);
  assert.equal(formatVolume(1_988_733), "1.99 tr");
  assert.equal(formatVolume(12_345), "12 ng");
  assert.equal(formatVolume(750), "750");
  assert.equal(formatVolume(null), "—");
});

test("whole-share counts degrade to a dash rather than NaN", () => {
  assert.equal(formatCount(null), "—");
  assert.equal(formatCount(10), "10");
  assert.match(formatCount(1_234_567), /^1[.,\s]?234[.,\s]?567$/);
  assert.equal(formatCount(0), "0");
  assert.equal(formatCount(Number.NaN), "—");
});

test("dates and provider timestamps are reshaped, never re-zoned", () => {
  assert.equal(formatShortDate("2026-10-15"), "15/10");
  assert.equal(formatLongDate("2026-10-15"), "15/10/2026");
  assert.equal(formatShortDate("not-a-date"), "not-a-date");
  assert.equal(formatMarketTime("2026-10-15T14:45:04+07:00"), "15/10 14:45");
  assert.equal(formatMarketTime(null), "—");
});
