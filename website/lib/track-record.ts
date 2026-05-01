// Public track-record data shapes — mirror the JSON written by
// `engines.website.build_track_record`. Keep these in sync; the
// exporter is the source of truth for the schema.

import fs from "fs/promises";
import path from "path";


export type Engine = "nrfi" | "props" | "full_game" | "parlay";
export type Tier = "LEAN" | "MODERATE" | "STRONG" | "ELITE";
export type ResultLabel = "W" | "L" | "Push" | "Pending";


export interface LedgerPick {
  engine: Engine;
  sport: string;            // 'MLB' for now
  market_type: string;
  pick_label: string;
  season: number;
  tier: Tier;
  predicted_p: number;      // 0..1
  predicted_pct: number;    // 0..100
  american_odds: number;
  actual_hit: boolean | null;   // null when pending
  units_delta: number;
  settled_at: string;       // ISO
  result: ResultLabel;
}


export interface LedgerFile {
  version: 1;
  generated_at: string;
  n_picks: number;
  picks: LedgerPick[];
}


export interface SummaryBucket {
  engine: Engine;
  season: number;
  tier: Tier;
  n_settled: number;
  wins: number;
  losses: number;
  pushes: number;
  units_won: number;
  hit_rate: number;     // 0..1
  hit_pct: number;      // 0..100
}


export interface SummaryFile {
  version: 1;
  generated_at: string;
  buckets: SummaryBucket[];
}


export interface DayRollup {
  date: string;
  n_settled: number;
  wins: number;
  losses: number;
  pushes: number;
  units_won: number;
}


export interface ByDayFile {
  version: 1;
  generated_at: string;
  days: DayRollup[];
}


export interface TrackRecordView {
  ledger: LedgerFile;
  summary: SummaryFile;
  byDay: ByDayFile;
  isPlaceholder: boolean;
}


// ---------------------------------------------------------------------------
// Server-side loader (called from getStaticProps)
// ---------------------------------------------------------------------------


const DATA_DIR = path.join(process.cwd(), "public", "data", "track-record");


/**
 * Load the three track-record JSON files. If any are missing — typical
 * for a fresh checkout before the engine has run the exporter — return
 * an empty placeholder bundle and flag `isPlaceholder=true` so the page
 * can render an honest "no data yet" state instead of erroring.
 */
export async function loadTrackRecord(): Promise<TrackRecordView> {
  try {
    const [ledgerRaw, summaryRaw, byDayRaw] = await Promise.all([
      fs.readFile(path.join(DATA_DIR, "ledger.json"), "utf-8"),
      fs.readFile(path.join(DATA_DIR, "summary.json"), "utf-8"),
      fs.readFile(path.join(DATA_DIR, "by-day.json"), "utf-8"),
    ]);
    return {
      ledger: JSON.parse(ledgerRaw),
      summary: JSON.parse(summaryRaw),
      byDay: JSON.parse(byDayRaw),
      isPlaceholder: false,
    };
  } catch {
    const now = new Date().toISOString();
    return {
      ledger: {
        version: 1, generated_at: now, n_picks: 0, picks: [],
      },
      summary: {
        version: 1, generated_at: now, buckets: [],
      },
      byDay: {
        version: 1, generated_at: now, days: [],
      },
      isPlaceholder: true,
    };
  }
}


// ---------------------------------------------------------------------------
// Tier color + ordering — keep in sync with engines/tiering.py constants.
// ---------------------------------------------------------------------------


export const TIER_ORDER: Tier[] = ["ELITE", "STRONG", "MODERATE", "LEAN"];


export const TIER_HEX: Record<Tier, string> = {
  // ELITE = Electric Blue, STRONG = Deep Green, MODERATE = Light Green,
  // LEAN = Yellow. Matches the rebrand from PR #96.
  ELITE: "#1e8fff",
  STRONG: "#1a7d3f",
  MODERATE: "#7bc063",
  LEAN: "#f4c430",
};


export const TIER_LABEL: Record<Tier, string> = {
  ELITE: "Elite",
  STRONG: "Strong",
  MODERATE: "Moderate",
  LEAN: "Lean",
};


export const ENGINE_LABEL: Record<Engine, string> = {
  nrfi: "First Inning",
  props: "Props",
  full_game: "Full Game",
  parlay: "Parlay",
};


// ---------------------------------------------------------------------------
// Aggregations the page renders
// ---------------------------------------------------------------------------


/** Combine summary buckets across seasons → one row per (engine, tier). */
export function bucketsByTier(summary: SummaryFile): SummaryBucket[] {
  const acc = new Map<string, SummaryBucket>();
  for (const b of summary.buckets) {
    const key = `${b.engine}::${b.tier}`;
    const cur = acc.get(key);
    if (cur) {
      cur.n_settled += b.n_settled;
      cur.wins += b.wins;
      cur.losses += b.losses;
      cur.pushes += b.pushes;
      cur.units_won += b.units_won;
    } else {
      // New row — copy via spread so we don't mutate the input.
      acc.set(key, { ...b });
    }
  }
  for (const b of acc.values()) {
    const denom = b.wins + b.losses;
    b.hit_rate = denom > 0 ? b.wins / denom : 0;
    b.hit_pct = Math.round(b.hit_rate * 1000) / 10;
  }
  return Array.from(acc.values());
}


/** All-engines roll-up by tier. */
export function bucketsByTierAcrossEngines(summary: SummaryFile): SummaryBucket[] {
  const acc = new Map<Tier, SummaryBucket>();
  for (const b of summary.buckets) {
    const cur = acc.get(b.tier);
    if (cur) {
      cur.n_settled += b.n_settled;
      cur.wins += b.wins;
      cur.losses += b.losses;
      cur.pushes += b.pushes;
      cur.units_won += b.units_won;
    } else {
      acc.set(b.tier, {
        engine: "nrfi",   // sentinel, ignored by the cross-engine view
        season: 0,
        tier: b.tier,
        n_settled: b.n_settled,
        wins: b.wins,
        losses: b.losses,
        pushes: b.pushes,
        units_won: b.units_won,
        hit_rate: 0,
        hit_pct: 0,
      });
    }
  }
  for (const b of acc.values()) {
    const denom = b.wins + b.losses;
    b.hit_rate = denom > 0 ? b.wins / denom : 0;
    b.hit_pct = Math.round(b.hit_rate * 1000) / 10;
  }
  return TIER_ORDER.map((t) => acc.get(t)).filter(
    (x): x is SummaryBucket => x !== undefined,
  );
}


export function formatAmericanOdds(o: number): string {
  if (!Number.isFinite(o) || o === 0) return "—";
  return o > 0 ? `+${Math.round(o)}` : `${Math.round(o)}`;
}
