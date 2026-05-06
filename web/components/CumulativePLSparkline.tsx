/**
 * Cumulative units P/L sparkline — pure inline SVG, zero deps.
 *
 * Lighter than RollingChart: no axis label, no per-point markers.
 * Used in the per-sport tile on the track-record page so a reader
 * can see the slope at a glance without zooming in.
 */

import type { DailyPLPoint } from "../lib/picks-history";


interface SparklineProps {
  series: DailyPLPoint[];
  height?: number;
  width?: number;
  emptyLabel?: string;
}


export function CumulativePLSparkline({
  series,
  height = 60,
  width = 240,
  emptyLabel = "No graded picks yet.",
}: SparklineProps) {
  if (!series || series.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-[11px] text-chalk-500"
        style={{ height }}
      >
        {emptyLabel}
      </div>
    );
  }
  const xs = series.map((_, i) => i);
  const ys = series.map((p) => p.cumulative_units);
  const padding = { top: 4, right: 4, bottom: 4, left: 4 };
  const minX = 0;
  const maxX = Math.max(xs[xs.length - 1], 1);
  const minY = Math.min(...ys, 0);
  const maxY = Math.max(...ys, 0);
  const xRange = maxX - minX || 1;
  const yRange = maxY - minY || 1;

  const sx = (x: number) =>
    padding.left + (x / xRange) * (width - padding.left - padding.right);
  const sy = (y: number) =>
    height - padding.bottom
    - ((y - minY) / yRange) * (height - padding.top - padding.bottom);

  const path = series
    .map((p, i) => `${i === 0 ? "M" : "L"} ${sx(i).toFixed(1)} ${sy(p.cumulative_units).toFixed(1)}`)
    .join(" ");
  const last = series[series.length - 1];
  const positive = last.cumulative_units >= 0;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Cumulative units P/L sparkline · ${series.length} dates`}
      className="w-full h-auto"
    >
      <line
        x1={padding.left}
        x2={width - padding.right}
        y1={sy(0)}
        y2={sy(0)}
        stroke="rgba(148, 163, 184, 0.35)"
        strokeDasharray="2 3"
        strokeWidth={1}
      />
      <path
        d={path}
        fill="none"
        stroke={positive ? "#22c55e" : "#ef4444"}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
