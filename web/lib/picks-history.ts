/**
 * Cross-sport picks-history loader + aggregator.
 *
 * Reads each sport's CLV-tracker output from
 * `public/data/<sport>/picks_log.json` (when present) and exposes:
 *
 *   - `loadAllPicks()`         — every pick across sports.
 *   - `summarizePicks()`       — wins/losses/units/CLV summary.
 *   - `dailyPLSeries()`        — chronological daily P/L for the
 *                                 track-record sparkline.
 *   - `byBetType()`            — group-by per bet_type with summary.
 *
 * Today only `mlb/picks_log.json` exists in production; the loader
 * still walks every sport so the day a WNBA / NFL / NCAAF tracker
 * starts shipping, the website picks it up automatically.
 */

import fs from "node:fs/promises";
import path from "node:path";

import { SPORT_LABEL, SPORTS, SportKey } from "./feed";


export interface PickRecord {
  sport: SportKey;
  pick_id: string;
  date: string;
  matchup: string;
  bet_type: string;
  pick: string;
  model_prob: number | null;
  edge_pct_at_pick: number | null;
  pick_price_dec: number | null;
  pick_price_american: number | null;
  closing_price_american: number | null;
  clv_pct: number | null;
  result: "WIN" | "LOSS" | "PUSH" | null;
  units: number | null;
  graded_at: string | null;
}


export interface PicksSummary {
  n: number;
  graded: number;
  wins: number;
  losses: number;
  pushes: number;
  hit_rate_pct: number;
  units_pl: number;
  total_stake: number;
  roi_pct: number;
  mean_clv_pct: number | null;
  n_with_clv: number;
}


export interface DailyPLPoint {
  date: string;
  daily_units: number;
  cumulative_units: number;
}


export interface BetTypeBreakdown {
  bet_type: string;
  summary: PicksSummary;
}


// ---------------------------------------------------------------------------
// Loader
// ---------------------------------------------------------------------------


export async function loadAllPicks(): Promise<PickRecord[]> {
  const out: PickRecord[] = [];
  for (const sport of SPORTS) {
    out.push(...(await loadSportPicks(sport)));
  }
  // Newest first by graded_at then date — keeps the ledger ordering
  // deterministic across reloads.
  out.sort((a, b) => {
    const aKey = a.graded_at ?? a.date;
    const bKey = b.graded_at ?? b.date;
    if (aKey === bKey) return 0;
    return aKey < bKey ? 1 : -1;
  });
  return out;
}


export async function loadSportPicks(sport: SportKey): Promise<PickRecord[]> {
  const file = path.join(
    process.cwd(), "public", "data", sport, "picks_log.json",
  );
  let raw: string;
  try {
    raw = await fs.readFile(file, "utf8");
  } catch {
    return [];
  }
  let parsed: { picks?: Array<Record<string, unknown>> };
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  return (parsed.picks ?? []).map((p) => ({
    sport,
    pick_id: String(p.pick_id ?? ""),
    date: String(p.date ?? ""),
    matchup: String(p.matchup ?? ""),
    bet_type: String(p.bet_type ?? ""),
    pick: String(p.pick ?? ""),
    model_prob: numOrNull(p.model_prob),
    edge_pct_at_pick: numOrNull(p.edge_pct_at_pick),
    pick_price_dec: numOrNull(p.pick_price_dec),
    pick_price_american: numOrNull(p.pick_price_american),
    closing_price_american: numOrNull(p.closing_price_american),
    clv_pct: numOrNull(p.clv_pct),
    result: parseResult(p.result),
    units: numOrNull(p.units),
    graded_at: typeof p.graded_at === "string" ? p.graded_at : null,
  }));
}


// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------


export function summarizePicks(picks: PickRecord[]): PicksSummary {
  const wins = picks.filter((p) => p.result === "WIN").length;
  const losses = picks.filter((p) => p.result === "LOSS").length;
  const pushes = picks.filter((p) => p.result === "PUSH").length;
  const graded = wins + losses;
  const totalUnits = picks.reduce(
    (s, p) => s + (typeof p.units === "number" ? p.units : 0),
    0,
  );
  // Stake = number of graded picks (1u flat-equivalent for ROI baseline).
  const stake = graded;
  const clvs = picks
    .map((p) => p.clv_pct)
    .filter((c): c is number => typeof c === "number");
  return {
    n: picks.length,
    graded,
    wins,
    losses,
    pushes,
    hit_rate_pct: graded > 0 ? +(wins / graded * 100).toFixed(1) : 0,
    units_pl: +totalUnits.toFixed(2),
    total_stake: stake,
    roi_pct: stake > 0 ? +((totalUnits / stake) * 100).toFixed(2) : 0,
    mean_clv_pct: clvs.length
      ? +(clvs.reduce((a, b) => a + b, 0) / clvs.length).toFixed(3)
      : null,
    n_with_clv: clvs.length,
  };
}


export function dailyPLSeries(picks: PickRecord[]): DailyPLPoint[] {
  const byDate = new Map<string, number>();
  for (const p of picks) {
    if (!p.date || typeof p.units !== "number") continue;
    byDate.set(p.date, (byDate.get(p.date) ?? 0) + p.units);
  }
  const sorted = Array.from(byDate.entries()).sort(
    ([a], [b]) => (a < b ? -1 : a > b ? 1 : 0),
  );
  let cum = 0;
  return sorted.map(([date, daily_units]) => {
    cum += daily_units;
    return {
      date,
      daily_units: +daily_units.toFixed(2),
      cumulative_units: +cum.toFixed(2),
    };
  });
}


export function byBetType(picks: PickRecord[]): BetTypeBreakdown[] {
  const groups = new Map<string, PickRecord[]>();
  for (const p of picks) {
    const list = groups.get(p.bet_type) ?? [];
    list.push(p);
    groups.set(p.bet_type, list);
  }
  return Array.from(groups.entries())
    .map(([bet_type, rows]) => ({
      bet_type,
      summary: summarizePicks(rows),
    }))
    .sort((a, b) => b.summary.n - a.summary.n);
}


export function bySport(picks: PickRecord[]): Map<SportKey, PickRecord[]> {
  const map = new Map<SportKey, PickRecord[]>();
  for (const sport of SPORTS) map.set(sport, []);
  for (const p of picks) {
    const list = map.get(p.sport) ?? [];
    list.push(p);
    map.set(p.sport, list);
  }
  return map;
}


// ---------------------------------------------------------------------------
// Top winners / losers — used by the track-record page.
// ---------------------------------------------------------------------------


export function topByUnits(
  picks: PickRecord[],
  options: { direction?: "winners" | "losers"; limit?: number } = {},
): PickRecord[] {
  const direction = options.direction ?? "winners";
  const limit = options.limit ?? 5;
  const graded = picks.filter(
    (p) => p.result !== null && typeof p.units === "number",
  );
  const sorted = graded.slice().sort((a, b) => {
    const au = a.units ?? 0;
    const bu = b.units ?? 0;
    return direction === "winners" ? bu - au : au - bu;
  });
  return sorted.slice(0, limit);
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function numOrNull(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}


function parseResult(v: unknown): "WIN" | "LOSS" | "PUSH" | null {
  if (v === "WIN" || v === "LOSS" || v === "PUSH") return v;
  return null;
}


/** Display label for the sport — re-exports from feed.ts for
 * downstream symmetry. */
export { SPORT_LABEL };
