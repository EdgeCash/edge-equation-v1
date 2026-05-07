import Link from "next/link";

import type { FeedPick, FeedParlay, SportKey } from "../lib/feed";
import { SPORT_LABEL } from "../lib/feed";
import { TierBadge, type ConvictionTier } from "./TierBadge";


// Python pipeline emits SHOUTY_SNAKE_CASE; the badge is keyed on the
// human "Signal Elite" form used in marketing copy.
const FEED_TIER_TO_BADGE: Record<string, ConvictionTier> = {
  ELITE: "Signal Elite",
  STRONG: "Strong Signal",
  MODERATE: "Moderate Signal",
  LEAN: "Lean Signal",
  NO_PLAY: "No Signal",
};


function feedTierToBadge(raw: string | null | undefined): ConvictionTier {
  return FEED_TIER_TO_BADGE[raw ?? ""] ?? "No Signal";
}


// Same map ``DailyCardView.tsx`` uses; intentionally duplicated so the
// home page renders without importing a client-only module. Keep them in
// sync if either is updated.
const MARKET_LABEL: Record<string, string> = {
  ML: "Moneyline",
  RUN_LINE: "Run Line",
  TOTAL: "Total",
  TEAM_TOTAL: "Team Total",
  FIRST_5: "First 5 Innings",
  NRFI: "First Inning",
  PLAYER_PROP_HITS: "Hits",
  PLAYER_PROP_TOTAL_BASES: "Total Bases",
  PLAYER_PROP_K: "Strikeouts",
  PLAYER_PROP_RBI: "RBIs",
  PLAYER_PROP_HR: "Home Runs",
  PLAYER_PROP_RUNS: "Runs",
  PLAYER_PROP_SB: "Stolen Bases",
};


interface Props {
  picks: FeedPick[];
  parlays: Array<{
    universe: "game_results" | "player_props";
    sport: SportKey;
    parlay: FeedParlay;
  }>;
  generatedAt: string;
}


/* Today's Top Edges --- the 3-8 picks promised on the home page,
 * front-and-center above the analytics strip. Cross-sport ranked by
 * (tier, edge desc); links into /daily-card for the full audit trail
 * + per-sport tabs + LEAN+ + CSV. */
export function TopEdgesHero({ picks, parlays, generatedAt }: Props) {
  if (picks.length === 0) {
    return (
      <section className="max-w-7xl mx-auto px-4 sm:px-6 py-12 sm:py-14">
        <SectionHeader />
        <div className="chalk-card p-8 sm:p-10 text-center">
          <p className="font-chalk text-3xl text-elite/80 -rotate-2 inline-block">
            No edges today.
          </p>
          <h3 className="mt-3 text-xl font-bold text-chalk-50">
            The math says pass.
          </h3>
          <p className="mt-2 text-sm text-chalk-300 max-w-xl mx-auto">
            None of today's slates produced a LEAN+ pick across our four
            sports. We'd rather sit out than force volume.
          </p>
          <div className="mt-6">
            <Link href="/daily-card" className="btn-ghost">
              See full daily card
            </Link>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="max-w-7xl mx-auto px-4 sm:px-6 py-12 sm:py-14">
      <SectionHeader count={picks.length} />

      <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {picks.map((p) => (
          <li key={p.id}>
            <PickCard pick={p} />
          </li>
        ))}
      </ul>

      {parlays.length > 0 && (
        <div className="mt-10">
          <div className="flex items-baseline justify-between mb-3">
            <h3 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
              Tonight&apos;s parlays
            </h3>
            <Link href="/parlays" className="text-xs text-elite hover:underline">
              See all tickets →
            </Link>
          </div>
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {parlays.map((entry, i) => (
              <li key={`${entry.sport}-${entry.universe}-${entry.parlay.id ?? i}`}>
                <ParlayCard
                  parlay={entry.parlay}
                  universe={entry.universe}
                  sport={entry.sport}
                />
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-8 flex flex-wrap items-center justify-between gap-4">
        <p className="text-[11px] font-mono text-chalk-500">
          ranked: tier × edge · feed: latest.json · {generatedAt}
        </p>
        <Link href="/daily-card" className="btn-primary">
          See every pick
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m9 18 6-6-6-6" />
          </svg>
        </Link>
      </div>
    </section>
  );
}


function SectionHeader({ count }: { count?: number }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <p className="font-chalk text-2xl text-elite/80 -rotate-1 inline-block">
          Today
        </p>
        <h2 className="mt-1 text-3xl sm:text-4xl font-bold text-chalk-50">
          Today&apos;s Top Edges
        </h2>
        <p className="mt-2 text-sm text-chalk-300 max-w-2xl">
          The 3–8 highest-conviction picks across MLB, WNBA, NFL, and NCAAF.
          Ranked by tier, then by edge over current market price.
        </p>
      </div>
      {count !== undefined && (
        <span className="text-[11px] font-mono text-chalk-500 uppercase tracking-wider">
          {count} {count === 1 ? "edge" : "edges"} today
        </span>
      )}
    </div>
  );
}


function PickCard({ pick }: { pick: FeedPick }) {
  const sport = (pick.sport ?? "mlb").toLowerCase() as SportKey;
  const sportLabel = SPORT_LABEL[sport] ?? pick.sport ?? "—";
  const marketLabel =
    MARKET_LABEL[pick.market_type] ?? pick.market_type.replace(/_/g, " ");
  return (
    <article className="chalk-card p-4 h-full flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[10px] font-mono text-chalk-500 uppercase tracking-wider">
            {sportLabel} · {marketLabel}
          </p>
          <p className="mt-1 text-base font-semibold text-chalk-50 leading-snug">
            {pick.selection}
          </p>
        </div>
        <TierBadge tier={feedTierToBadge(pick.tier)} size="sm" />
      </div>
      <p className="mt-2 text-[11px] font-mono text-chalk-400 line-clamp-2">
        {pick.notes}
      </p>
      <div className="mt-auto pt-3 flex items-center justify-between text-xs font-mono">
        <span className="text-chalk-300">{fmtAmerican(pick.line.odds)}</span>
        <span className="text-chalk-500">
          edge {pctOrPct(pick.edge)} · {pctOrPct(pick.kelly)}u
        </span>
      </div>
    </article>
  );
}


function ParlayCard({
  parlay,
  universe,
  sport,
}: {
  parlay: FeedParlay;
  universe: "game_results" | "player_props";
  sport: SportKey;
}) {
  const sportLabel = SPORT_LABEL[sport] ?? sport.toUpperCase();
  const universeLabel =
    universe === "game_results" ? "Game-results" : "Player-props";
  return (
    <article className="border border-chalkboard-700/60 rounded p-3 text-xs h-full flex flex-col">
      <p className="text-[10px] font-mono text-chalk-500 uppercase tracking-wider">
        {sportLabel} · {universeLabel}
      </p>
      <div className="mt-1 flex items-baseline justify-between">
        <span className="font-mono text-elite">
          {parlay.n_legs}-leg @ {fmtAmerican(parlay.combined_american_odds)}
        </span>
        <span className="text-chalk-500 font-mono text-[10px]">
          edge {pctOrPct(parlay.edge_pp)}
        </span>
      </div>
      <ul className="mt-2 space-y-0.5 text-chalk-300 flex-1">
        {parlay.legs.slice(0, 3).map((leg, i) => (
          <li key={i} className="truncate">
            • {leg.selection}
          </li>
        ))}
        {parlay.legs.length > 3 && (
          <li className="text-chalk-500">+{parlay.legs.length - 3} more</li>
        )}
      </ul>
    </article>
  );
}


function fmtAmerican(odds: number): string {
  if (!Number.isFinite(odds) || odds === 0) return "n/a";
  return odds > 0 ? `+${Math.round(odds)}` : `${Math.round(odds)}`;
}


function pctOrPct(v: string | number): string {
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}`;
}
