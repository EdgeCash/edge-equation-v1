"use client";

import { useState, useMemo } from "react";
import Link from "next/link";

import type { FeedParlay, FeedPick, SportKey } from "../lib/feed";
import { TierBadge, type ConvictionTier } from "./TierBadge";


// The Python pipeline emits SHOUTY snake-case tiers; the badge component
// is keyed on the human "Signal Elite" form used in marketing copy.
const TIER_MAP: Record<string, ConvictionTier> = {
  ELITE: "Signal Elite",
  STRONG: "Strong Signal",
  MODERATE: "Moderate Signal",
  LEAN: "Lean Signal",
  NO_PLAY: "No Signal",
};


function feedTierToBadge(raw: string | null | undefined): ConvictionTier {
  if (!raw) return "No Signal";
  return TIER_MAP[raw] ?? "No Signal";
}


/* ============================================================ */
/* Public daily-card view --- single source of truth.           */
/*                                                              */
/* Reads the unified ``latest.json`` feed (picks across every   */
/* sport + parlays) and lays it out as:                         */
/*                                                              */
/*   - Sport tab strip                                          */
/*   - Tier filter toggle (ELITE / STRONG default; LEAN+ all)   */
/*   - Picks table (grouped by market_type within sport)        */
/*   - "Download CSV" button (every LEAN+ pick the engine       */
/*     graded today, audit trail material)                      */
/*   - Parlays preview block                                    */
/*                                                              */
/* The legacy MLB-only render is now just one of four sport     */
/* tabs; nothing else hangs off ``mlb_daily.json::tabs``.       */
/* ============================================================ */


export const SPORTS_DISPLAY: ReadonlyArray<{
  key: SportKey;
  label: string;
}> = [
  { key: "mlb", label: "MLB" },
  { key: "wnba", label: "WNBA" },
  { key: "nfl", label: "NFL" },
  { key: "ncaaf", label: "NCAAF" },
];


type SportPicksMap = Record<SportKey, FeedPick[]>;
type SportParlaysMap = Record<SportKey, {
  game_results: FeedParlay[];
  player_props: FeedParlay[];
}>;


interface Props {
  picksBySport: SportPicksMap;
  parlaysBySport: SportParlaysMap;
  generatedAt: string;
}


type TierFilter = "elite_strong" | "lean_plus";


export function DailyCardView({
  picksBySport, parlaysBySport, generatedAt,
}: Props) {
  // The first sport with at least one pick is the default tab so the
  // user lands on something with content. WNBA / NFL / NCAAF will show
  // an empty state when their slates are dry, which is fine but not the
  // first thing a casual visitor should see.
  const defaultSport = useMemo<SportKey>(() => {
    for (const { key } of SPORTS_DISPLAY) {
      if ((picksBySport[key]?.length ?? 0) > 0) return key;
    }
    return "mlb";
  }, [picksBySport]);

  const [activeSport, setActiveSport] = useState<SportKey>(defaultSport);
  const [tierFilter, setTierFilter] = useState<TierFilter>("elite_strong");

  const allPicks = picksBySport[activeSport] ?? [];
  const visiblePicks = useMemo(() => {
    if (tierFilter === "elite_strong") {
      return allPicks.filter((p) =>
        p.tier === "ELITE" || p.tier === "STRONG"
      );
    }
    return allPicks; // LEAN+ default of the upstream filter
  }, [allPicks, tierFilter]);

  const grouped = useMemo(() => groupByMarket(visiblePicks), [visiblePicks]);

  const parlays = parlaysBySport[activeSport] ?? {
    game_results: [],
    player_props: [],
  };
  const totalParlays = parlays.game_results.length + parlays.player_props.length;

  return (
    <section className="max-w-7xl mx-auto px-4 sm:px-6 py-10 sm:py-14">
      {/* Sport tab strip */}
      <div className="flex flex-wrap gap-2 mb-6">
        {SPORTS_DISPLAY.map(({ key, label }) => {
          const count = picksBySport[key]?.length ?? 0;
          const active = key === activeSport;
          return (
            <button
              key={key}
              onClick={() => setActiveSport(key)}
              className={
                "px-3 py-1.5 rounded text-sm font-mono transition-colors " +
                (active
                  ? "bg-elite/20 text-elite border border-elite/50"
                  : "bg-chalkboard-800/60 text-chalk-300 border border-chalkboard-700/60 hover:text-elite hover:border-elite/40")
              }
            >
              {label}{" "}
              <span className="text-[10px] text-chalk-500">({count})</span>
            </button>
          );
        })}
      </div>

      {/* Tier filter + CSV download */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div className="flex gap-1.5">
          <FilterChip
            active={tierFilter === "elite_strong"}
            label="ELITE + STRONG"
            onClick={() => setTierFilter("elite_strong")}
          />
          <FilterChip
            active={tierFilter === "lean_plus"}
            label="All LEAN+"
            onClick={() => setTierFilter("lean_plus")}
          />
        </div>
        <CsvDownloadButton picks={allPicks} sport={activeSport} />
      </div>

      {/* Picks --- empty state, or grouped by market */}
      {visiblePicks.length === 0 ? (
        <EmptyState
          sport={activeSport}
          totalForSport={allPicks.length}
          onLoosenFilter={() => setTierFilter("lean_plus")}
        />
      ) : (
        <div className="space-y-6">
          {grouped.map(({ marketType, label, picks }) => (
            <MarketGroup
              key={marketType}
              label={label}
              picks={picks}
            />
          ))}
        </div>
      )}

      {/* Parlay preview --- 1-line stats + link to /parlays */}
      {totalParlays > 0 && (
        <div className="mt-10 chalk-card p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
              Tonight's parlays
            </h2>
            <Link
              href="/parlays"
              className="text-xs text-elite hover:underline"
            >
              See all →
            </Link>
          </div>
          <p className="mt-2 text-xs text-chalk-500">
            {parlays.game_results.length} game-results ticket
            {parlays.game_results.length === 1 ? "" : "s"} ·{" "}
            {parlays.player_props.length} player-props ticket
            {parlays.player_props.length === 1 ? "" : "s"}
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {[...parlays.game_results, ...parlays.player_props]
              .slice(0, 3)
              .map((p, i) => (
                <ParlayPreviewCard key={p.id ?? `parlay-${i}`} parlay={p} />
              ))}
          </div>
        </div>
      )}

      <p className="mt-8 text-[10px] font-mono text-chalk-500 border-t border-chalkboard-800/60 pt-4">
        feed: latest.json · {generatedAt}
      </p>
    </section>
  );
}


/* ---------- Pieces ---------- */


function FilterChip({
  active, label, onClick,
}: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={
        "text-[11px] font-mono px-2 py-1 rounded transition-colors " +
        (active
          ? "bg-elite/20 text-elite border border-elite/50"
          : "bg-chalkboard-800/60 text-chalk-300 border border-chalkboard-700/60 hover:text-elite hover:border-elite/40")
      }
    >
      {label}
    </button>
  );
}


function MarketGroup({
  label, picks,
}: { label: string; picks: FeedPick[] }) {
  return (
    <div className="chalk-card p-4 sm:p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-xs uppercase tracking-wider text-chalk-300 font-mono">
          {label}
        </h3>
        <span className="text-[10px] text-chalk-500 font-mono">
          {picks.length} {picks.length === 1 ? "pick" : "picks"}
        </span>
      </div>
      <ul className="space-y-2">
        {picks.map((p) => (
          <li
            key={p.id}
            className="flex flex-wrap items-baseline justify-between gap-2 text-sm border-b border-chalkboard-800/40 last:border-b-0 pb-2 last:pb-0"
          >
            <div className="min-w-0 flex-1">
              <p className="text-chalk-100 font-medium">{p.selection}</p>
              <p className="text-[11px] font-mono text-chalk-500 truncate">
                {p.notes}
              </p>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <span className="text-[11px] font-mono text-chalk-300">
                {fmtAmerican(p.line.odds)}
              </span>
              <TierBadge tier={feedTierToBadge(p.tier)} size="sm" />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}


function ParlayPreviewCard({ parlay }: { parlay: FeedParlay }) {
  return (
    <div className="border border-chalkboard-700/60 rounded p-3 text-xs">
      <div className="flex items-baseline justify-between mb-1">
        <span className="font-mono text-elite">
          {parlay.n_legs}-leg @ {fmtAmericanStr(parlay.combined_american_odds)}
        </span>
        <span className="text-chalk-500 font-mono text-[10px]">
          edge {pctOrPct(parlay.edge_pp)}
        </span>
      </div>
      <ul className="space-y-0.5 text-chalk-300">
        {parlay.legs.slice(0, 3).map((leg, i) => (
          <li key={i} className="truncate">
            • {leg.selection}
          </li>
        ))}
        {parlay.legs.length > 3 && (
          <li className="text-chalk-500">
            +{parlay.legs.length - 3} more
          </li>
        )}
      </ul>
    </div>
  );
}


function EmptyState({
  sport, totalForSport, onLoosenFilter,
}: {
  sport: SportKey;
  totalForSport: number;
  onLoosenFilter: () => void;
}) {
  if (totalForSport === 0) {
    return (
      <div className="chalk-card p-8 text-center">
        <h2 className="text-xl font-bold text-chalk-50">
          No {sport.toUpperCase()} plays today
        </h2>
        <p className="mt-2 text-sm text-chalk-300 max-w-md mx-auto">
          The engine ran for this slate and produced no qualifying
          edges, or the slate hasn't published yet. Volume without
          edge is how bankrolls die — we'll surface a play when one
          appears.
        </p>
      </div>
    );
  }
  return (
    <div className="chalk-card p-6 text-center">
      <p className="text-sm text-chalk-300">
        {totalForSport} {sport.toUpperCase()} pick
        {totalForSport === 1 ? "" : "s"} graded today, but none meet the
        ELITE / STRONG bar.
      </p>
      <button
        onClick={onLoosenFilter}
        className="mt-3 text-[11px] font-mono px-3 py-1.5 rounded bg-elite/10 text-elite border border-elite/30 hover:bg-elite/20 transition-colors"
      >
        Show all LEAN+
      </button>
    </div>
  );
}


function CsvDownloadButton({
  picks, sport,
}: { picks: FeedPick[]; sport: SportKey }) {
  return (
    <button
      onClick={() => downloadCsv(picks, sport)}
      disabled={picks.length === 0}
      className="text-[11px] font-mono px-2.5 py-1 rounded border border-chalkboard-700/60 text-chalk-300 hover:text-elite hover:border-elite/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      title="Download every LEAN+ pick the engine graded today"
    >
      ↓ Download CSV
    </button>
  );
}


/* ---------- Helpers ---------- */


// Display label per market_type. New market types fall through to a
// title-cased version of the slug --- no need to update this map for
// every minor variant the engines might surface.
const MARKET_LABEL: Record<string, string> = {
  ML: "Moneyline",
  RUN_LINE: "Run Line",
  TOTAL: "Total",
  TEAM_TOTAL: "Team Total",
  FIRST_5: "First 5 Innings",
  NRFI: "First Inning (NRFI/YRFI)",
  PLAYER_PROP_HITS: "Player Props · Hits",
  PLAYER_PROP_TOTAL_BASES: "Player Props · Total Bases",
  PLAYER_PROP_K: "Player Props · Strikeouts",
  PLAYER_PROP_RBI: "Player Props · RBIs",
  PLAYER_PROP_HR: "Player Props · Home Runs",
  PLAYER_PROP_RUNS: "Player Props · Runs",
  PLAYER_PROP_SB: "Player Props · Stolen Bases",
};


// Sort the market groups: NRFI first (the user's NASCAR pitch),
// then game-result markets, then player props alphabetical. Within
// a group, picks come in by edge desc.
const MARKET_ORDER: string[] = [
  "NRFI",
  "ML", "RUN_LINE", "TOTAL", "TEAM_TOTAL", "FIRST_5",
];


function groupByMarket(
  picks: FeedPick[],
): Array<{ marketType: string; label: string; picks: FeedPick[] }> {
  const buckets = new Map<string, FeedPick[]>();
  for (const p of picks) {
    const key = p.market_type ?? "OTHER";
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key)!.push(p);
  }
  // Sort each bucket by numeric edge desc.
  for (const v of buckets.values()) {
    v.sort((a, b) => Number(b.edge ?? 0) - Number(a.edge ?? 0));
  }
  // Order the buckets themselves: known game-result markets first,
  // then everything else alphabetical.
  const knownOrder = MARKET_ORDER;
  const ranked: Array<{ marketType: string; label: string; picks: FeedPick[] }> = [];
  for (const mk of knownOrder) {
    if (buckets.has(mk)) {
      ranked.push({
        marketType: mk,
        label: MARKET_LABEL[mk] ?? mk,
        picks: buckets.get(mk)!,
      });
      buckets.delete(mk);
    }
  }
  const tail = Array.from(buckets.entries()).sort(([a], [b]) => a.localeCompare(b));
  for (const [mk, ps] of tail) {
    ranked.push({
      marketType: mk,
      label: MARKET_LABEL[mk] ?? prettyLabel(mk),
      picks: ps,
    });
  }
  return ranked;
}


function prettyLabel(slug: string): string {
  return slug
    .replace(/^PLAYER_PROP_/, "Player Props · ")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}


function fmtAmerican(odds: number): string {
  if (odds === 0) return "n/a";
  return odds > 0 ? `+${Math.round(odds)}` : `${Math.round(odds)}`;
}


function fmtAmericanStr(odds: number): string {
  return fmtAmerican(odds);
}


function pctOrPct(v: string | number): string {
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}pp`;
}


function downloadCsv(picks: FeedPick[], sport: SportKey): void {
  if (picks.length === 0) return;
  // Order columns to match what a power user importing into a model
  // would expect first. The audit-trail fields (id, edge, fair_prob,
  // kelly, tier, grade, notes) come last so the human-eyeball columns
  // (selection, market_type, line, odds) lead.
  const cols: Array<keyof FeedPick | "line_number" | "line_odds"> = [
    "sport", "market_type", "selection", "line_number", "line_odds",
    "tier", "grade", "fair_prob", "edge", "kelly",
    "event_time", "game_id", "id", "notes",
  ];
  const header = cols.map(csvEscape).join(",");
  const lines = picks.map((p) => cols.map((c) => {
    if (c === "line_number") return csvEscape(p.line?.number ?? "");
    if (c === "line_odds") return csvEscape(p.line?.odds ?? "");
    return csvEscape((p as unknown as Record<string, unknown>)[c as string]);
  }).join(","));
  const csv = [header, ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const today = new Date().toISOString().slice(0, 10);
  const a = document.createElement("a");
  a.href = url;
  a.download = `edge-equation-${sport}-${today}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}


function csvEscape(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (s.includes(",") || s.includes("\"") || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}
