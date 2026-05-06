/**
 * Unified daily-feed loader.
 *
 * Reads the JSON the per-sport pipelines write to
 * `public/data/<sport>/...` plus the unified daily feed at
 * `public/data/daily/latest.json`. All loaders are best-effort — a
 * missing file returns null so a single broken pipeline can never
 * blank the whole homepage.
 *
 * Server-side use only (called from Server Components + the search
 * index builder). Client components hydrate via the `?` API routes
 * (next phase) or the embedded JSON snapshot the page passes down.
 */

import fs from "node:fs/promises";
import path from "node:path";

export type SportKey = "mlb" | "wnba" | "nfl" | "ncaaf";

export interface FeedParlayLeg {
  market_type: string;
  selection: string;
  line_odds: number;
  side_probability: string;
  tier: string;
}

export interface FeedParlay {
  id: string;
  universe: string;
  n_legs: number;
  combined_decimal_odds: number;
  combined_american_odds: number;
  fair_decimal_odds: number;
  joint_prob_corr: string;
  joint_prob_independent: string;
  implied_prob: string;
  edge_pp: string;
  ev_units: string;
  stake_units: number;
  note: string;
  legs: FeedParlayLeg[];
}

export interface FeedPick {
  id: string;
  sport: string;
  market_type: string;
  selection: string;
  line: { number: string | null; odds: number };
  fair_prob: string;
  edge: string;
  kelly: string;
  grade: string;
  tier: string | null;
  notes: string;
  event_time: string | null;
  game_id: string;
}

export interface DailyFeedSection {
  picks?: FeedPick[];
  parlays?: {
    transparency_note?: string;
    game_results?: FeedParlay[];
    player_props?: FeedParlay[];
    no_qualified_message?: Record<string, string>;
  };
}

export interface DailyFeed {
  version: number;
  generated_at: string;
  footer?: string;
  date: string;
  source: string;
  notes: string;
  picks: FeedPick[];
  parlays?: {
    transparency_note?: string;
    game_results?: FeedParlay[];
    player_props?: FeedParlay[];
    no_qualified_message?: Record<string, string>;
  };
  wnba?: DailyFeedSection;
  nfl?: DailyFeedSection;
  ncaaf?: DailyFeedSection;
  market_status?: Record<string, string>;
}

export interface BacktestSummary {
  version: number;
  sport?: string;
  target_date: string;
  generated_at: string;
  windows: string[];
  transparency_note: string;
  per_market: Record<
    string,
    { n: number; roi_pct: number; brier: number; clv_pp: number }
  >;
  parlays: {
    game_results: ParlayHighlight;
    player_props: ParlayHighlight;
  };
  feature_flag?: { name: string; default: string; note: string };
}

export interface ParlayHighlight {
  label: string;
  n_slates: number;
  n_tickets: number;
  units_pl: number;
  roi_pct: number;
  brier: number;
  avg_joint_prob: number;
  no_qualified_pct: number;
  avg_clv_pp: number;
  hit_rate_pct: number;
  avg_legs: number;
}


// ---------------------------------------------------------------------------
// Filesystem-backed loaders. Vercel ships `public/` as static assets, so
// fetching at build time is the cheapest way to read them.
// ---------------------------------------------------------------------------


function publicDataPath(...parts: string[]): string {
  // The build script copies website/public/data → web/public/data on every
  // build; reading from web/public/data here keeps server components fast.
  return path.join(process.cwd(), "public", "data", ...parts);
}


async function readJson<T>(relPath: string): Promise<T | null> {
  try {
    const raw = await fs.readFile(publicDataPath(relPath), "utf8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}


/** Load the unified daily feed (the one written by run_daily_*.py --all). */
export async function getDailyFeed(): Promise<DailyFeed | null> {
  return readJson<DailyFeed>(path.join("daily", "latest.json"));
}


/** Load a sport's backtest summary (per-sport JSON written by the
 * sport's backtest CLI). */
export async function getBacktestSummary(
  sport: SportKey,
): Promise<BacktestSummary | null> {
  return readJson<BacktestSummary>(path.join(sport, "backtest_summary.json"));
}


/** Load the legacy MLB daily spreadsheet snapshot (for the existing
 * /daily-card route). */
export async function getMLBDaily(): Promise<unknown | null> {
  return readJson(path.join("mlb", "mlb_daily.json"));
}


// ---------------------------------------------------------------------------
// Convenience selectors
// ---------------------------------------------------------------------------


/** All picks for a given sport, including game and prop rows. */
export function picksForSport(
  feed: DailyFeed | null, sport: SportKey,
): FeedPick[] {
  if (!feed) return [];
  if (sport === "mlb") return feed.picks ?? [];
  return (feed[sport]?.picks ?? []) as FeedPick[];
}


/** Game-results parlays for a given sport. */
export function gameParlaysForSport(
  feed: DailyFeed | null, sport: SportKey,
): FeedParlay[] {
  if (!feed) return [];
  if (sport === "mlb") return feed.parlays?.game_results ?? [];
  return (feed[sport]?.parlays?.game_results ?? []) as FeedParlay[];
}


/** Player-props parlays for a given sport. */
export function propParlaysForSport(
  feed: DailyFeed | null, sport: SportKey,
): FeedParlay[] {
  if (!feed) return [];
  if (sport === "mlb") return feed.parlays?.player_props ?? [];
  return (feed[sport]?.parlays?.player_props ?? []) as FeedParlay[];
}


export const SPORTS: readonly SportKey[] = ["mlb", "wnba", "nfl", "ncaaf"] as const;


export const SPORT_LABEL: Record<SportKey, string> = {
  mlb: "MLB",
  wnba: "WNBA",
  nfl: "NFL",
  ncaaf: "NCAAF",
};


export const SPORT_ACCENT: Record<SportKey, string> = {
  mlb: "text-elite",
  wnba: "text-strong",
  nfl: "text-moderate",
  ncaaf: "text-lean",
};
