/**
 * Reliability diagram (calibration plot) — pure inline SVG.
 *
 * Bins picks by predicted probability (`model_prob`) and plots the
 * realized hit rate within each bin against the diagonal y = x. The
 * audit's "show your work" rule: a model that says "70%" should win
 * 70% of the time over enough samples; drift away from the diagonal
 * is exactly what we want a reader to see.
 *
 * Pure SVG, server-rendered, no chart library.
 */

import type { PickRecord } from "../lib/picks-history";


interface Bin {
  lo: number;
  hi: number;
  n: number;
  graded: number;
  wins: number;
}


interface ReliabilityDiagramProps {
  picks: PickRecord[];
  height?: number;
  width?: number;
  bins?: number;
  emptyLabel?: string;
}


export function ReliabilityDiagram({
  picks,
  height = 280,
  width = 400,
  bins: nBins = 10,
  emptyLabel = "No graded picks yet — calibration plot needs ≥10 graded picks per bin.",
}: ReliabilityDiagramProps) {
  const bins = bucketPicks(picks, nBins);
  const totalGraded = bins.reduce((s, b) => s + b.graded, 0);

  if (totalGraded === 0) {
    return (
      <div
        className="flex items-center justify-center rounded border border-chalkboard-700/60 bg-chalkboard-900/60 text-xs text-chalk-500 px-4 text-center"
        style={{ height }}
      >
        {emptyLabel}
      </div>
    );
  }

  const padding = { top: 12, right: 12, bottom: 32, left: 36 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;
  const sx = (v: number) => padding.left + v * innerW;
  const sy = (v: number) => padding.top + (1 - v) * innerH;

  // Diagonal — perfect calibration
  const diagPath = `M ${sx(0)} ${sy(0)} L ${sx(1)} ${sy(1)}`;
  // Observed reliability curve
  const points = bins
    .filter((b) => b.graded > 0)
    .map((b) => {
      const meanPred = (b.lo + b.hi) / 2;
      const actual = b.wins / b.graded;
      return { x: meanPred, y: actual, n: b.graded };
    });
  const linePath = points
    .map(
      (p, i) =>
        `${i === 0 ? "M" : "L"} ${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`,
    )
    .join(" ");

  return (
    <figure className="rounded border border-chalkboard-700/60 bg-chalkboard-900/60 p-2 overflow-hidden">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Reliability diagram · ${totalGraded} graded picks`}
        className="w-full h-auto"
      >
        {/* Y axis ticks */}
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={`yt-${v}`}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={sy(v)}
              y2={sy(v)}
              stroke="rgba(255, 255, 255, 0.06)"
              strokeWidth={1}
            />
            <text
              x={padding.left - 4}
              y={sy(v) + 3}
              textAnchor="end"
              fontSize="9"
              fontFamily="ui-monospace, monospace"
              fill="rgba(148, 163, 184, 0.85)"
            >
              {(v * 100).toFixed(0)}%
            </text>
          </g>
        ))}
        {/* X axis ticks */}
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={`xt-${v}`}>
            <text
              x={sx(v)}
              y={height - 12}
              textAnchor="middle"
              fontSize="9"
              fontFamily="ui-monospace, monospace"
              fill="rgba(148, 163, 184, 0.85)"
            >
              {(v * 100).toFixed(0)}%
            </text>
          </g>
        ))}
        {/* Axis labels */}
        <text
          x={width / 2}
          y={height - 2}
          textAnchor="middle"
          fontSize="10"
          fontFamily="ui-monospace, monospace"
          fill="rgba(148, 163, 184, 0.85)"
        >
          Predicted probability
        </text>
        <text
          x={-height / 2}
          y={10}
          transform="rotate(-90)"
          textAnchor="middle"
          fontSize="10"
          fontFamily="ui-monospace, monospace"
          fill="rgba(148, 163, 184, 0.85)"
        >
          Realized hit rate
        </text>
        {/* Diagonal */}
        <path
          d={diagPath}
          stroke="rgba(148, 163, 184, 0.35)"
          strokeDasharray="3 3"
          strokeWidth={1}
          fill="none"
        />
        {/* Observed reliability line */}
        {linePath && (
          <path
            d={linePath}
            fill="none"
            stroke="#38bdf8"
            strokeWidth={1.6}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )}
        {/* Bin dots — radius scales with sample size */}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={sx(p.x)}
            cy={sy(p.y)}
            r={Math.max(2.5, Math.min(7, Math.sqrt(p.n) * 0.6))}
            fill="#38bdf8"
            opacity={0.85}
          >
            <title>
              Bucket {(p.x * 100).toFixed(0)}% · {p.n} graded · realized{" "}
              {(p.y * 100).toFixed(1)}%
            </title>
          </circle>
        ))}
      </svg>
      <figcaption className="mt-1 text-[10px] text-chalk-500 px-2 pb-1">
        {totalGraded} graded picks · {bins.filter((b) => b.graded > 0).length}{" "}
        / {nBins} buckets populated · dot size scales with bucket sample
      </figcaption>
    </figure>
  );
}


function bucketPicks(picks: PickRecord[], nBins: number): Bin[] {
  const bins: Bin[] = [];
  const step = 1 / nBins;
  for (let i = 0; i < nBins; i++) {
    const lo = i * step;
    const hi = i === nBins - 1 ? 1.0001 : (i + 1) * step;
    bins.push({ lo, hi, n: 0, graded: 0, wins: 0 });
  }
  for (const p of picks) {
    if (typeof p.model_prob !== "number") continue;
    const idx = Math.min(
      nBins - 1,
      Math.max(0, Math.floor(p.model_prob * nBins)),
    );
    bins[idx].n += 1;
    if (p.result === "WIN") {
      bins[idx].graded += 1;
      bins[idx].wins += 1;
    } else if (p.result === "LOSS") {
      bins[idx].graded += 1;
    }
  }
  return bins;
}


/** Brier score across the picks. Returned for the page header next to
 * the diagram so a reader can pair the visual with the scalar. */
export function brierScore(picks: PickRecord[]): {
  score: number | null;
  n: number;
} {
  const graded = picks.filter(
    (p) =>
      (p.result === "WIN" || p.result === "LOSS")
      && typeof p.model_prob === "number",
  );
  if (graded.length === 0) return { score: null, n: 0 };
  const sum = graded.reduce((s, p) => {
    const out = p.result === "WIN" ? 1 : 0;
    const diff = (p.model_prob ?? 0) - out;
    return s + diff * diff;
  }, 0);
  return { score: +(sum / graded.length).toFixed(4), n: graded.length };
}
