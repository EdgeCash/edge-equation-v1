/**
 * Player + team profile resolver.
 *
 * Profile data is assembled from the data the engines already publish:
 *
 *   - `daily/latest.json`              — today's picks across sports
 *   - `<sport>/backtest_summary.json`  — engine performance snapshot
 *   - `mlb/picks_log.json`             — graded MLB pick history (CLV
 *                                         tracker output)
 *
 * The audit-locked rule of the engines applies here too: when a
 * specific data slice isn't available for a name (e.g. a WNBA player
 * with no graded picks yet), the profile page renders an honest
 * "Limited Data" panel rather than fabricated stats. Game logs land
 * once the per-sport ingestion layer surfaces them; until then the
 * profile page exposes the engine's history of picks ON the player
 * (every CLV-tracked pick) which IS available.
 */

import {
  DailyFeed,
  FeedPick,
  SportKey,
  getDailyFeed,
  getBacktestSummary,
  picksForSport,
  BacktestSummary,
} from "./feed";
import { parseSelection, slugify } from "./search-index";
import path from "node:path";
import fs from "node:fs/promises";


export interface PlayerProfile {
  sport: SportKey;
  id: string;
  display: string;
  todays_picks: FeedPick[];
  // Engine history: every pick the CLV tracker logged on this player.
  history_records: PickHistoryRow[];
  history_summary: PickHistorySummary;
  // Trend table: rolling per-pick edge / CLV across history (for the
  // small inline chart on the profile page).
  trend: { x: number; y: number; label: string }[];
}


export interface TeamProfile {
  sport: SportKey;
  id: string;
  display: string;
  todays_picks: FeedPick[];
  history_records: PickHistoryRow[];
  history_summary: PickHistorySummary;
  trend: { x: number; y: number; label: string }[];
}


export interface PickHistoryRow {
  date: string;
  matchup: string;
  bet_type: string;
  pick: string;
  edge_pp?: number | null;
  clv_pp?: number | null;
  result?: "WIN" | "LOSS" | "PUSH" | null;
  units?: number | null;
}


export interface PickHistorySummary {
  n: number;
  graded: number;
  wins: number;
  losses: number;
  pushes: number;
  hit_rate_pct: number;
  units_pl: number;
  roi_pct: number;
  mean_clv_pp: number | null;
  mean_edge_pp: number | null;
}


// ---------------------------------------------------------------------------
// Public resolvers
// ---------------------------------------------------------------------------


export async function resolvePlayerProfile(
  sport: SportKey, id: string,
): Promise<PlayerProfile | null> {
  const feed = await getDailyFeed();
  const todays = picksForSport(feed, sport).filter((p) => {
    const { player } = parseSelection(p);
    return player && slugify(player) === id;
  });
  const display = todays[0]
    ? parseSelection(todays[0]).player ?? id
    : prettifySlug(id);

  const history = await loadPicksHistory();
  const matched = history.filter((row) =>
    rowMentionsName(row, display),
  );
  const summary = summarizeHistory(matched);
  const trend = trendFromHistory(matched);

  return {
    sport, id, display,
    todays_picks: todays,
    history_records: matched,
    history_summary: summary,
    trend,
  };
}


export async function resolveTeamProfile(
  sport: SportKey, id: string,
): Promise<TeamProfile | null> {
  const feed = await getDailyFeed();
  const todays = picksForSport(feed, sport).filter((p) => {
    const { team } = parseSelection(p);
    return team && slugify(team) === id;
  });
  const display = todays[0]
    ? parseSelection(todays[0]).team ?? id.toUpperCase()
    : id.toUpperCase();

  const history = await loadPicksHistory();
  const matched = history.filter((row) =>
    rowMentionsName(row, display) || rowMentionsName(row, id.toUpperCase()),
  );
  const summary = summarizeHistory(matched);
  const trend = trendFromHistory(matched);

  return {
    sport, id, display,
    todays_picks: todays,
    history_records: matched,
    history_summary: summary,
    trend,
  };
}


// ---------------------------------------------------------------------------
// Engine-history loader
//
// Today the only graded pick log is MLB's `mlb/picks_log.json` (CLV
// tracker output, see exporters.mlb.clv_tracker.ClvTracker). When the
// WNBA / NFL / NCAAF pipelines start writing the same shape into
// `<sport>/picks_log.json`, this loader picks them up automatically.
// ---------------------------------------------------------------------------


export interface RawPickLogEntry {
  pick_id?: string;
  date?: string;
  matchup?: string;
  bet_type?: string;
  pick?: string;
  model_prob?: number;
  edge_pct_at_pick?: number;
  kelly_pct?: number;
  pick_price_dec?: number;
  pick_price_american?: number;
  closing_price_dec?: number | null;
  closing_price_american?: number | null;
  clv_pct?: number | null;
  result?: "WIN" | "LOSS" | "PUSH" | null;
  units?: number | null;
}


async function loadPicksHistory(): Promise<PickHistoryRow[]> {
  const all: PickHistoryRow[] = [];
  for (const sport of ["mlb", "wnba", "nfl", "ncaaf"] as const) {
    const file = path.join(
      process.cwd(), "public", "data", sport, "picks_log.json",
    );
    try {
      const raw = await fs.readFile(file, "utf8");
      const parsed = JSON.parse(raw) as { picks?: RawPickLogEntry[] };
      for (const p of parsed.picks ?? []) {
        all.push({
          date: String(p.date ?? ""),
          matchup: String(p.matchup ?? ""),
          bet_type: String(p.bet_type ?? ""),
          pick: String(p.pick ?? ""),
          edge_pp: typeof p.edge_pct_at_pick === "number"
            ? p.edge_pct_at_pick : null,
          clv_pp: typeof p.clv_pct === "number" ? p.clv_pct : null,
          result: p.result ?? null,
          units: p.units ?? null,
        });
      }
    } catch {
      // No picks log for this sport yet — quietly skip.
    }
  }
  // Newest first.
  all.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  return all;
}


function rowMentionsName(row: PickHistoryRow, name: string): boolean {
  if (!name) return false;
  const haystack = `${row.matchup} ${row.pick} ${row.bet_type}`.toLowerCase();
  return haystack.includes(name.toLowerCase());
}


function summarizeHistory(rows: PickHistoryRow[]): PickHistorySummary {
  const wins = rows.filter((r) => r.result === "WIN").length;
  const losses = rows.filter((r) => r.result === "LOSS").length;
  const pushes = rows.filter((r) => r.result === "PUSH").length;
  const graded = wins + losses;
  const units_pl = rows.reduce(
    (s, r) => s + (typeof r.units === "number" ? r.units : 0), 0,
  );
  const clvs = rows
    .map((r) => r.clv_pp)
    .filter((c): c is number => typeof c === "number");
  const edges = rows
    .map((r) => r.edge_pp)
    .filter((e): e is number => typeof e === "number");
  return {
    n: rows.length,
    graded,
    wins,
    losses,
    pushes,
    hit_rate_pct: graded ? Math.round((wins / graded) * 1000) / 10 : 0,
    units_pl: Math.round(units_pl * 100) / 100,
    roi_pct: rows.length ? Math.round((units_pl / rows.length) * 1000) / 10 : 0,
    mean_clv_pp: clvs.length
      ? Math.round((clvs.reduce((a, b) => a + b, 0) / clvs.length) * 100) / 100
      : null,
    mean_edge_pp: edges.length
      ? Math.round((edges.reduce((a, b) => a + b, 0) / edges.length) * 100) / 100
      : null,
  };
}


function trendFromHistory(rows: PickHistoryRow[]) {
  // Reverse to chronological so the chart reads left→right oldest→newest.
  const chronological = rows.slice().reverse();
  return chronological
    .filter((r) => typeof r.edge_pp === "number")
    .map((r, i) => ({
      x: i + 1,
      y: Math.round((r.edge_pp ?? 0) * 100) / 100,
      label: `${r.date} · ${r.pick}`,
    }));
}


function prettifySlug(id: string): string {
  return id
    .split("-")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(" ");
}


// ---------------------------------------------------------------------------
// Per-sport engine snapshot — used in the profile sidebar.
// ---------------------------------------------------------------------------


export async function getEngineSnapshot(
  sport: SportKey,
): Promise<BacktestSummary | null> {
  return getBacktestSummary(sport);
}
