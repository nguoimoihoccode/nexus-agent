import { useMemo } from "react";

import type { MarketBar } from "../../api/marketQuotes.js";
import {
  CHART_HEIGHT,
  CHART_PADDING,
  CHART_WIDTH,
  VOLUME_BOTTOM,
  dateTicks,
  formatLongDate,
  formatVnd,
  formatVolume,
  maxVolume,
  priceDomain,
  pricePoints,
  priceTicks,
  volumeBars,
} from "./chartGeometry.js";

type PriceChartProps = {
  symbol: string;
  rangeLabel: string;
  bars: readonly MarketBar[];
};

const TABLE_ROWS = 30;

export function PriceChart({ symbol, rangeLabel, bars }: PriceChartProps) {
  const chart = useMemo(() => {
    const domain = priceDomain(bars.map((bar) => bar.close));
    return {
      points: pricePoints(bars, domain),
      ticks: priceTicks(domain),
      volumes: volumeBars(bars, maxVolume(bars)),
      dates: dateTicks(bars),
    };
  }, [bars]);

  if (bars.length === 0) {
    return (
      <p className="market-empty-note" role="status">
        Không có phiên giao dịch nào trong khoảng thời gian này.
      </p>
    );
  }

  const right = CHART_WIDTH - CHART_PADDING.right;

  return (
    <figure className="market-chart">
      <svg
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        role="img"
        aria-label={`Biểu đồ giá đóng cửa ${symbol} trong ${rangeLabel}, ${bars.length} phiên.`}
      >
        <line
          className="market-chart-axis"
          x1={CHART_PADDING.left}
          y1={VOLUME_BOTTOM}
          x2={right}
          y2={VOLUME_BOTTOM}
        />
        {chart.ticks.map((tick) => (
          <g key={tick.value}>
            <line
              className="market-chart-grid"
              x1={CHART_PADDING.left}
              y1={tick.y}
              x2={right}
              y2={tick.y}
            />
            <text className="market-chart-labels" x={right + 6} y={tick.y + 3}>
              {formatVnd(Math.round(tick.value))}
            </text>
          </g>
        ))}
        {chart.volumes.map((bar) => (
          <rect
            key={bar.x}
            className="market-volume-bar"
            x={bar.x}
            y={bar.y}
            width={bar.width}
            height={bar.height}
          />
        ))}
        <polyline className="market-chart-line" points={chart.points} />
        {chart.dates.map((tick) => (
          <text
            key={tick.index}
            className="market-chart-labels"
            x={tick.x}
            y={CHART_HEIGHT - 8}
            textAnchor="middle"
          >
            {tick.label}
          </text>
        ))}
      </svg>

      {/* Text alternative: a screen reader cannot read a polyline, and the repo
          runs axe with a zero serious/critical budget. */}
      <details className="market-series-table">
        <summary>Bảng giá đóng cửa ({bars.length} phiên)</summary>
        <table>
          <caption>
            {symbol} — {rangeLabel}
          </caption>
          <thead>
            <tr>
              <th scope="col">Phiên</th>
              <th scope="col">Đóng cửa (VND)</th>
              <th scope="col">Khối lượng</th>
            </tr>
          </thead>
          <tbody>
            {bars.slice(-TABLE_ROWS).reverse().map((bar) => (
              <tr key={bar.time}>
                <th scope="row">{formatLongDate(bar.time)}</th>
                <td>{formatVnd(bar.close)}</td>
                <td>{formatVolume(bar.volume)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
