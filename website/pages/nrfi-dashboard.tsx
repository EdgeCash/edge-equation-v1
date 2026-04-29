import type { GetServerSideProps } from "next";

import CardShell from "@/components/CardShell";
import Layout from "@/components/Layout";
import StatTile from "@/components/StatTile";
import { api } from "@/lib/api";
import type {
  NrfiBoardRow,
  NrfiDashboard,
  NrfiTier,
  NrfiTierLedgerRow,
  ParlayCandidate,
} from "@/lib/types";


type Props = {
  data: NrfiDashboard | null;
  error: string | null;
};


export const getServerSideProps: GetServerSideProps<Props> = async (ctx) => {
  const date = typeof ctx.query.date === "string" ? ctx.query.date : undefined;
  try {
    const data = await api.nrfiDashboard(date);
    return { props: { data, error: null } };
  } catch (e: unknown) {
    return {
      props: {
        data: null,
        error: e instanceof Error ? e.message : "unknown error",
      },
    };
  }
};


// ---------------------------------------------------------------------------
// Tier badge — colors mirror the Python TIER_COLOR_HEX so the dashboard
// stays visually identical to the daily email + Discord card.
// ---------------------------------------------------------------------------

const TIER_BG: Record<NrfiTier, string> = {
  LOCK:     "bg-emerald-700 text-white",
  STRONG:   "bg-lime-500 text-ink-950",
  MODERATE: "bg-amber-400 text-ink-950",
  LEAN:     "bg-orange-500 text-white",
  NO_PLAY:  "bg-rose-700 text-white",
};


function TierBadge({ tier }: { tier: NrfiTier | undefined }) {
  if (!tier) return null;
  const cls = TIER_BG[tier] ?? "bg-edge-line text-edge-text";
  return (
    <span
      className={
        "inline-block px-2 py-0.5 font-mono text-[10px] uppercase " +
        "tracking-[0.18em] rounded-sm " + cls
      }
    >
      {tier}
    </span>
  );
}


// ---------------------------------------------------------------------------
// Board — one row per game with NRFI/YRFI tier badges side by side.
// ---------------------------------------------------------------------------

function BoardRow({ row }: { row: NrfiBoardRow }) {
  const matchup = row.away_team && row.home_team
    ? `${row.away_team} @ ${row.home_team}`
    : `gamePk ${row.game_pk}`;
  const nrfiPct = typeof row.nrfi_pct === "number"
    ? row.nrfi_pct.toFixed(1) + "%"
    : "—";
  const yrfiPct = typeof row.nrfi_pct === "number"
    ? (100 - row.nrfi_pct).toFixed(1) + "%"
    : "—";
  return (
    <div className="grid grid-cols-12 items-center gap-2 py-3 border-b border-edge-line last:border-b-0">
      <div className="col-span-12 sm:col-span-5 font-mono text-sm text-edge-text">
        {matchup}
      </div>
      <div className="col-span-6 sm:col-span-3 flex items-center gap-2">
        <TierBadge tier={row.nrfi_tier} />
        <span className="font-mono tabular-nums text-edge-textDim text-sm">
          NRFI {nrfiPct}
        </span>
      </div>
      <div className="col-span-6 sm:col-span-3 flex items-center gap-2">
        <TierBadge tier={row.yrfi_tier} />
        <span className="font-mono tabular-nums text-edge-textDim text-sm">
          YRFI {yrfiPct}
        </span>
      </div>
      <div className="col-span-12 sm:col-span-1 text-right font-mono text-[11px] text-edge-textDim tabular-nums">
        λ={typeof row.lambda_total === "number" ? row.lambda_total.toFixed(2) : "—"}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Ledger — one StatTile per tier per market, showing W-L + units.
// ---------------------------------------------------------------------------

function LedgerTile({ row }: { row: NrfiTierLedgerRow }) {
  const record = `${row.wins}-${row.losses}`;
  const units = `${row.units_won >= 0 ? "+" : ""}${row.units_won.toFixed(2)}u`;
  const roi = row.n_settled > 0
    ? ((row.units_won / row.n_settled) * 100).toFixed(1) + "%"
    : "—";
  const label = row.tier === "ALL"
    ? `${row.market_type} · TOTAL`
    : `${row.market_type} · ${row.tier}`;
  return (
    <StatTile
      label={label}
      value={units}
      subValue={`${record}  ROI ${roi}`}
    />
  );
}


// ---------------------------------------------------------------------------
// Parlay candidate card.
// ---------------------------------------------------------------------------

function ParlayCard({ candidate, idx }: { candidate: ParlayCandidate; idx: number }) {
  const americanCombined = candidate.combined_american_odds;
  const americanStr = Number.isFinite(americanCombined) && americanCombined !== 0
    ? (americanCombined > 0 ? "+" + Math.round(americanCombined) : Math.round(americanCombined).toString())
    : "—";
  return (
    <div className="border border-edge-line rounded-sm p-5 bg-ink-900/60">
      <div className="flex items-baseline justify-between mb-3">
        <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-accent">
          Special Drop · #{idx + 1}
        </div>
        <div className="font-mono tabular-nums text-edge-text">
          {candidate.combined_decimal_odds.toFixed(2)}x
          <span className="text-edge-textDim ml-2">({americanStr})</span>
        </div>
      </div>
      <div className="space-y-1">
        {candidate.legs.map((leg, i) => (
          <div key={i} className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <TierBadge tier={leg.tier} />
              <span className="font-mono text-sm text-edge-text">
                {leg.label}
              </span>
            </div>
            <div className="font-mono tabular-nums text-[11px] text-edge-textDim">
              {(leg.side_probability * 100).toFixed(1)}%  ·  {leg.american_odds > 0 ? "+" : ""}{leg.american_odds}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3 text-[11px] font-mono tabular-nums">
        <div>
          <div className="text-edge-textDim uppercase tracking-wider">Joint (corr)</div>
          <div className="text-edge-text">
            {(candidate.joint_prob_corr * 100).toFixed(1)}%
          </div>
        </div>
        <div>
          <div className="text-edge-textDim uppercase tracking-wider">Implied</div>
          <div className="text-edge-text">
            {(candidate.implied_prob * 100).toFixed(1)}%
          </div>
        </div>
        <div>
          <div className="text-edge-textDim uppercase tracking-wider">Edge</div>
          <div className="text-edge-text">
            {candidate.edge_pp >= 0 ? "+" : ""}{candidate.edge_pp.toFixed(1)}pp
          </div>
        </div>
        <div>
          <div className="text-edge-textDim uppercase tracking-wider">EV @ {candidate.stake_units.toFixed(2)}u</div>
          <div className="text-edge-text">
            {candidate.ev_units >= 0 ? "+" : ""}{candidate.ev_units.toFixed(3)}u
          </div>
        </div>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function NrfiDashboardPage({ data, error }: Props) {
  return (
    <Layout
      title="NRFI Dashboard"
      description="Today's first-inning board, per-tier YTD ledger, and Special Drop parlay candidates."
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        NRFI / YRFI · Live
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        NRFI Dashboard
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        Today&apos;s first-inning board with tier-tagged conviction, the
        per-tier YTD ledger from Phase 3, and the qualifying
        cross-market Special Drop candidates from the parlay engine.
      </p>

      {error && (
        <div className="mt-10 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.2em] text-[10px] mb-2">
            API Error
          </div>
          <p className="text-edge-text font-mono text-sm">{error}</p>
          <p className="mt-3 text-edge-textDim text-sm">
            If you&apos;re running locally, make sure the FastAPI backend is
            up and <code className="font-mono">NEXT_PUBLIC_API_BASE_URL</code>{" "}
            points at it.
          </p>
        </div>
      )}

      {data && (
        <div className="mt-10 space-y-10">
          {/* Today's board */}
          <CardShell
            eyebrow={`Slate · ${data.date}`}
            headline={`${data.board.length} game${data.board.length === 1 ? "" : "s"}`}
            subhead="NRFI on the left, YRFI on the right. Tier badges drive conviction."
          >
            {data.board.length === 0 ? (
              <p className="text-edge-textDim">
                No predictions stored yet for this date. The daily ETL +
                engine pass populates this table around 9 AM CT.
              </p>
            ) : (
              <div>
                {data.board.map((row) => (
                  <BoardRow key={row.game_pk} row={row} />
                ))}
              </div>
            )}
          </CardShell>

          {/* Per-tier YTD ledger */}
          <CardShell
            eyebrow="Phase 3 · YTD"
            headline="Per-Tier Ledger"
            subhead="Independent W-L and unit return per tier. The single grading system."
          >
            {data.ytd_ledger.length === 0 ? (
              <p className="text-edge-textDim">
                No picks settled yet this season — first ledger entries
                land the day after the first qualifying game finishes.
              </p>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
                {data.ytd_ledger.map((row, i) => (
                  <LedgerTile key={i} row={row} />
                ))}
              </div>
            )}
          </CardShell>

          {/* Special Drop candidates */}
          <CardShell
            eyebrow="Phase 6 · Parlays"
            headline="Special Drop Candidates"
            subhead="STRONG-and-above legs only · joint prob ≥ 68% (correlation-adjusted) · EV ≥ +0.25u at 0.5u stake."
          >
            {data.parlay_candidates.length === 0 ? (
              <p className="text-edge-textDim">
                No qualifying combinations on today&apos;s slate — the
                strict gates are designed for 0–2 candidates per week.
              </p>
            ) : (
              <div className="space-y-4">
                {data.parlay_candidates.map((c, i) => (
                  <ParlayCard key={i} candidate={c} idx={i} />
                ))}
              </div>
            )}
          </CardShell>

          {/* Parlay ledger summary */}
          <CardShell
            eyebrow="Phase 6 · Ledger"
            headline="Parlay Units"
            subhead="Units only — no win-loss accounting."
          >
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatTile
                label="Recorded"
                value={String(data.parlay_ledger.recorded)}
                subValue={`${data.parlay_ledger.settled} settled · ${data.parlay_ledger.pending} pending`}
              />
              <StatTile
                label="Units Returned"
                value={`${data.parlay_ledger.units_returned >= 0 ? "+" : ""}${data.parlay_ledger.units_returned.toFixed(2)}u`}
              />
              <StatTile
                label="Total Stake"
                value={`${data.parlay_ledger.total_stake.toFixed(2)}u`}
              />
              <StatTile
                label="ROI"
                value={`${data.parlay_ledger.roi_pct >= 0 ? "+" : ""}${data.parlay_ledger.roi_pct.toFixed(1)}%`}
              />
            </div>
          </CardShell>
        </div>
      )}
    </Layout>
  );
}
