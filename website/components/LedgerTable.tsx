import { useMemo, useState } from "react";

import ResultBadge from "@/components/ResultBadge";
import {
  ENGINE_LABEL, TIER_LABEL, TIER_ORDER, formatAmericanOdds,
  type Engine, type LedgerPick, type Tier,
} from "@/lib/track-record";


type Props = { picks: LedgerPick[] };


type EngineFilter = "all" | Engine;
type TierFilter = "all" | Tier;
type ResultFilter = "all" | "W" | "L" | "Push" | "Pending";


const PAGE_SIZE = 50;


// Map tier → V4 conviction-token classes for the row chip.
const TIER_CHIP: Record<Tier, string> = {
  ELITE:    "bg-conviction-eliteSoft text-conviction-elite",
  STRONG:   "bg-conviction-strongSoft text-conviction-strong",
  MODERATE: "bg-conviction-moderateSoft text-conviction-moderate",
  LEAN:     "bg-conviction-neutralSoft text-edge-textDim",
};


/**
 * Public ledger table. Mobile-first stacked rows; on desktop expands
 * to a 6-column grid. Filters live above; pagination at 50/page below.
 */
export default function LedgerTable({ picks }: Props) {
  const [engine, setEngine] = useState<EngineFilter>("all");
  const [tier, setTier] = useState<TierFilter>("all");
  const [result, setResult] = useState<ResultFilter>("all");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    return picks.filter((p) => {
      if (engine !== "all" && p.engine !== engine) return false;
      if (tier !== "all" && p.tier !== tier) return false;
      if (result !== "all" && p.result !== result) return false;
      return true;
    });
  }, [picks, engine, tier, result]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const visible = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  return (
    <div className="space-y-4">
      {/* ----- Filters ----- */}
      <div className="flex flex-wrap items-end gap-3">
        <FilterSelect
          label="Engine"
          value={engine}
          onChange={(v) => { setEngine(v as EngineFilter); setPage(0); }}
          options={[
            ["all", "All engines"],
            ["nrfi", ENGINE_LABEL.nrfi],
            ["props", ENGINE_LABEL.props],
            ["full_game", ENGINE_LABEL.full_game],
            ["parlay", ENGINE_LABEL.parlay],
          ]}
        />
        <FilterSelect
          label="Tier"
          value={tier}
          onChange={(v) => { setTier(v as TierFilter); setPage(0); }}
          options={[
            ["all", "All tiers"],
            ...TIER_ORDER.map((t) => [t, TIER_LABEL[t]] as [string, string]),
          ]}
        />
        <FilterSelect
          label="Result"
          value={result}
          onChange={(v) => { setResult(v as ResultFilter); setPage(0); }}
          options={[
            ["all", "All results"],
            ["W", "Wins"],
            ["L", "Losses"],
            ["Push", "Pushes"],
            ["Pending", "Pending"],
          ]}
        />
        <div className="ml-auto self-end font-mono text-[11px] text-edge-textFaint">
          {filtered.length} pick{filtered.length === 1 ? "" : "s"}
        </div>
      </div>

      {/* ----- Empty state ----- */}
      {filtered.length === 0 && (
        <div className="rounded-sm border border-edge-line bg-ink-900/80 p-6 text-center text-sm text-edge-textDim">
          No picks match these filters yet.
        </div>
      )}

      {/* ----- Rows ----- */}
      {visible.length > 0 && (
        <div className="overflow-hidden rounded-sm border border-edge-line bg-ink-900/80">
          {/* Desktop header — hidden on mobile, the stacked row IS the header on mobile. */}
          <div className="hidden grid-cols-[100px_1fr_120px_70px_70px_60px] gap-3 border-b border-edge-line px-4 py-2 font-mono text-[10px] uppercase tracking-[0.24em] text-edge-textFaint md:grid">
            <span>Date</span>
            <span>Pick</span>
            <span>Tier</span>
            <span>Conviction</span>
            <span>Odds</span>
            <span className="text-right">Result</span>
          </div>
          <ul>
            {visible.map((p, i) => (
              <Row key={`${p.engine}-${p.settled_at}-${i}`} pick={p} />
            ))}
          </ul>
        </div>
      )}

      {/* ----- Pagination ----- */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between gap-2 text-xs">
          <button
            disabled={safePage === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            className="rounded-sm border border-edge-line bg-ink-900/80 px-3 py-1 font-mono text-[11px] uppercase tracking-wider text-edge-textDim disabled:opacity-40"
          >
            ← Newer
          </button>
          <span className="font-mono text-[11px] text-edge-textFaint">
            Page {safePage + 1} of {totalPages}
          </span>
          <button
            disabled={safePage >= totalPages - 1}
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            className="rounded-sm border border-edge-line bg-ink-900/80 px-3 py-1 font-mono text-[11px] uppercase tracking-wider text-edge-textDim disabled:opacity-40"
          >
            Older →
          </button>
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------


function Row({ pick }: { pick: LedgerPick }) {
  const date = (pick.settled_at || "").slice(0, 10);
  return (
    <li className="border-b border-edge-line last:border-b-0">
      <div className="block px-4 py-3 md:grid md:grid-cols-[100px_1fr_120px_70px_70px_60px] md:items-center md:gap-3">
        <span className="font-mono text-[11px] text-edge-textFaint md:text-[12px] md:text-edge-textDim">
          {date}
        </span>
        <div className="mt-1 md:mt-0">
          <div className="text-sm text-edge-text">{pick.pick_label}</div>
          <div className="font-mono text-[10px] uppercase tracking-wider text-edge-textFaint">
            {ENGINE_LABEL[pick.engine]} · {pick.market_type}
          </div>
        </div>
        <span
          className={`mt-2 inline-block rounded-sm px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider md:mt-0 ${TIER_CHIP[pick.tier]}`}
        >
          {TIER_LABEL[pick.tier]}
        </span>
        <span className="mt-1 block font-mono text-[11px] text-edge-textDim md:mt-0 md:text-[12px]">
          {pick.predicted_pct.toFixed(1)}%
        </span>
        <span className="font-mono text-[11px] text-edge-textDim md:text-[12px]">
          {formatAmericanOdds(pick.american_odds)}
        </span>
        <span className="mt-2 block text-right md:mt-0">
          <ResultBadge result={pick.result} />
        </span>
      </div>
    </li>
  );
}


function FilterSelect({
  label, value, onChange, options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: ReadonlyArray<[string, string]>;
}) {
  return (
    <label className="flex flex-col gap-1 font-mono text-[10px] uppercase tracking-[0.24em] text-edge-textFaint">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-sm border border-edge-line bg-ink-900/80 px-2 py-1 text-xs text-edge-text focus:border-edge-accent focus:outline-none"
      >
        {options.map(([v, lbl]) => (
          <option key={v} value={v}>{lbl}</option>
        ))}
      </select>
    </label>
  );
}
