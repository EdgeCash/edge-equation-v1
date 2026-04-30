// Single row in the picks table / list. Shared by the daily-edge and
// slate-detail pages. Conviction tier drives the visual treatment.

import type { ArchivedPick, ApiPick } from "@/lib/types";
import {
  formatAmericanOdds,
  formatNumber,
  formatPercent,
} from "@/lib/api";
import { tierFromGrade, CONVICTION } from "@/lib/conviction";
import ConvictionBadge from "./ConvictionBadge";

type PickRowProps = {
  pick: ApiPick | ArchivedPick;
};

export default function PickRow({ pick }: PickRowProps) {
  const tier = tierFromGrade(pick.grade);
  const meta = CONVICTION[tier];
  const isElite = tier === "ELITE";
  const lineText = pick.line.number
    ? `${pick.line.number} @ ${formatAmericanOdds(pick.line.odds)}`
    : formatAmericanOdds(pick.line.odds);

  return (
    <div
      className={[
        "rounded-sm p-5 grid grid-cols-12 gap-4 items-center bg-ink-900/40 border",
        meta.borderClass,
        isElite ? "shadow-elite-glow" : "",
      ].join(" ")}
    >
      <div className="col-span-12 sm:col-span-3 flex items-center gap-3">
        <ConvictionBadge tier={tier} />
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textDim">
          {pick.sport}
        </span>
      </div>
      <div className="col-span-12 sm:col-span-4">
        <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
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
        <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">
          {pick.fair_prob ? "Fair" : pick.expected_value ? "Expected" : ""}
        </div>
        <div className="mt-1 font-mono tabular-nums text-edge-text">
          {pick.fair_prob
            ? formatPercent(pick.fair_prob, 2)
            : formatNumber(pick.expected_value, 2)}
        </div>
      </div>
      <div className="col-span-6 sm:col-span-2">
        <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">
          Edge · ½ Kelly
        </div>
        <div className={"mt-1 font-mono tabular-nums " + (isElite ? "text-conviction-elite" : "text-edge-text")}>
          {formatPercent(pick.edge)} · {formatPercent(pick.kelly)}
        </div>
      </div>
      <div className="col-span-12 sm:col-span-1 text-right">
        <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">
          Real.
        </div>
        <div className="mt-1 font-mono tabular-nums text-edge-text">
          {pick.realization}
        </div>
      </div>
    </div>
  );
}
