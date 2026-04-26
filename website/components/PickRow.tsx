// Single row in the picks table / list. Shared by the daily-edge and
// slate-detail pages.

import type { ArchivedPick, ApiPick } from "@/lib/types";
import {
  formatAmericanOdds,
  formatNumber,
  formatPercent,
} from "@/lib/api";
import GradeBadge from "./GradeBadge";

type PickRowProps = {
  pick: ApiPick | ArchivedPick;
};

export default function PickRow({ pick }: PickRowProps) {
  const lineText = pick.line.number
    ? `${pick.line.number} @ ${formatAmericanOdds(pick.line.odds)}`
    : formatAmericanOdds(pick.line.odds);

  return (
    <div
      className={
        "relative border border-edge-line rounded-sm p-5 pl-6 " +
        "grid grid-cols-12 gap-4 items-center bg-ink-900/40 " +
        "hover:border-edge-accent/50 hover:bg-ink-900/70 transition-colors group"
      }
    >
      {/* Left cyan accent bar — slim, brightens on hover */}
      <span
        aria-hidden
        className="absolute left-0 top-0 bottom-0 w-px bg-edge-accent/40 group-hover:bg-edge-accent transition-colors"
      />

      <div className="col-span-12 sm:col-span-2 flex items-center gap-3">
        <GradeBadge grade={pick.grade} />
        <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-edge-textDim">
          {pick.sport}
        </span>
      </div>
      <div className="col-span-12 sm:col-span-5">
        <div className="annotation">
          <span className="text-edge-accent">{"// "}</span>
          {pick.market_type}
        </div>
        <div className="mt-1 font-display text-lg tracking-tightest text-edge-text">
          {pick.selection}
        </div>
        <div className="mt-1 font-mono text-xs tabular-nums text-edge-textDim">
          {lineText}
        </div>
      </div>
      <div className="col-span-6 sm:col-span-2">
        <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
          {pick.fair_prob ? "Fair" : pick.expected_value ? "Expected" : ""}
        </div>
        <div className="mt-1 font-mono tabular-nums text-edge-text">
          {pick.fair_prob
            ? formatPercent(pick.fair_prob, 2)
            : formatNumber(pick.expected_value, 2)}
        </div>
      </div>
      <div className="col-span-6 sm:col-span-2">
        <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
          Edge · ½ Kelly
        </div>
        <div className="mt-1 font-mono tabular-nums text-edge-text">
          {formatPercent(pick.edge)} · {formatPercent(pick.kelly)}
        </div>
      </div>
      <div className="col-span-12 sm:col-span-1 text-right">
        <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
          Real.
        </div>
        <div className="mt-1 font-mono tabular-nums text-edge-text">
          {pick.realization}
        </div>
      </div>
    </div>
  );
}
