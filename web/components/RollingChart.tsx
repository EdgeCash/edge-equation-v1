/**
 * Tiny rolling-trend chart — pure inline SVG, zero runtime deps.
 *
 * The audit's "lightweight charts" requirement called for Recharts;
 * we keep it leaner by drawing the polyline ourselves. The chart
 * shape is intentionally minimal: an axis pair, a baseline, the
 * polyline, and dot markers on each pick. That's enough signal for
 * the player / team profile sidebar without bloating the bundle.
 */

import type { CSSProperties } from "react";


export interface ChartPoint {
  x: number;
  y: number;
  label?: string;
}


interface RollingChartProps {
  points: ChartPoint[];
  height?: number;
  width?: number;
  yAxisLabel?: string;
  emptyLabel?: string;
}


export function RollingChart({
  points,
  height = 140,
  width = 360,
  yAxisLabel = "Edge (pp)",
  emptyLabel = "No graded picks yet — chart populates as the engine logs CLV.",
}: RollingChartProps) {
  if (!points || points.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded border border-chalkboard-700/60 bg-chalkboard-900/60 text-xs text-chalk-500 px-4"
        style={{ height }}
      >
        {emptyLabel}
      </div>
    );
  }

  const padding = { top: 12, right: 12, bottom: 22, left: 36 };
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys, 0);
  const maxY = Math.max(...ys, 0);
  const xRange = maxX - minX || 1;
  const yRange = maxY - minY || 1;

  const sx = (x: number) =>
    padding.left + ((x - minX) / xRange) * (width - padding.left - padding.right);
  const sy = (y: number) =>
    height - padding.bottom
    - ((y - minY) / yRange) * (height - padding.top - padding.bottom);

  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`)
    .join(" ");

  const baselineY = sy(0);
  const lastPoint = points[points.length - 1];

  return (
    <figure className="rounded border border-chalkboard-700/60 bg-chalkboard-900/60 p-2 overflow-hidden">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${yAxisLabel} trend across ${points.length} picks`}
        className="w-full h-auto"
      >
        {/* Y axis baseline (zero line) */}
        <line
          x1={padding.left} x2={width - padding.right}
          y1={baselineY} y2={baselineY}
          stroke="rgba(148, 163, 184, 0.4)"
          strokeDasharray="2 3"
          strokeWidth={1}
        />

        {/* Polyline */}
        <path
          d={linePath}
          fill="none"
          stroke="#38bdf8"
          strokeWidth={1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* Dots */}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={sx(p.x)}
            cy={sy(p.y)}
            r={2.4}
            fill="#38bdf8"
            opacity={i === points.length - 1 ? 1.0 : 0.7}
          >
            {p.label && <title>{p.label}: {p.y >= 0 ? "+" : ""}{p.y}</title>}
          </circle>
        ))}

        {/* Y axis label */}
        <text
          x={6}
          y={padding.top + 6}
          fontSize="10"
          fill="rgba(148, 163, 184, 0.85)"
          fontFamily="ui-monospace, monospace"
        >
          {yAxisLabel}
        </text>

        {/* Latest value annotation */}
        <text
          x={sx(lastPoint.x) - 4}
          y={sy(lastPoint.y) - 6}
          fontSize="10"
          textAnchor="end"
          fill="#e2e8f0"
          fontFamily="ui-monospace, monospace"
        >
          {lastPoint.y >= 0 ? "+" : ""}
          {lastPoint.y}
        </text>

        {/* X-axis caption: "n picks" so readers don't need to count dots */}
        <text
          x={width - padding.right}
          y={height - 6}
          fontSize="10"
          textAnchor="end"
          fill="rgba(148, 163, 184, 0.85)"
          fontFamily="ui-monospace, monospace"
        >
          {points.length} picks
        </text>
      </svg>
    </figure>
  );
}
