// Schema + loader for the unified daily picks feed.
//
// The website reads picks from a static JSON file produced by run_daily.py
// at public/data/daily/latest.json. This is the primary data source. If the
// file is missing or malformed, the loader falls back to the FastAPI archive
// (api.latestSlate("daily_edge")) so the page never goes dark during the
// public-testing rollout.
//
// The schema is intentionally narrower than the full archive payload — only
// the fields the UI actually renders. Numeric fields stay as strings to
// preserve Decimal precision over JSON.

import fs from "node:fs/promises";
import path from "node:path";

import { api } from "./api";
import type { ArchivedPick, SlateDetail } from "./types";

export const DAILY_FEED_PATH = "../public/data/mlb/mlb_daily.json";
// Fixed for Vercel Root Directory = website/ + vercel.json
export const DAILY_FEED_VERSION = 1;

// ---------------------------------------------------------------------------
// Wire types — the on-disk JSON.
// ---------------------------------------------------------------------------

export interface DailyFeedPick {
  /** Stable id within the slate. game_pk + market or similar. */
  id: string;
  sport: string;                                  // "MLB", "NFL", ...
  market_type: string;                            // "NRFI", "PLAYER_HITS_OVER_0.5", "MONEYLINE", ...
  selection: string;                              // display string
  line: { number: string | null; odds: number | null };
  fair_prob: string | null;                       // decimals as strings
  edge: string | null;
  kelly: string | null;
  grade: string;                                  // "A+" | "A" | "B" | "C" | "D" | "F"
  /** Optional explicit ConvictionTier override. Otherwise derived from grade. */
  tier?: string | null;
  /** The Why paragraph (nullable; premium is where this is full). */
  notes?: string | null;
  /** Optional event time (ISO). */
  event_time?: string | null;
  /** Optional game id. */
  game_id?: string | null;
}

export interface DailyFeed {
  version: number;                                // === DAILY_FEED_VERSION
  generated_at: string;                           // ISO datetime
  date: string;                                   // YYYY-MM-DD slate date
  source: string;                                 // "run_daily.py"
  notes?: string | null;
  picks: DailyFeedPick[];
}

// ---------------------------------------------------------------------------
// Display type — what the daily-edge page actually renders.
// ---------------------------------------------------------------------------

export interface DailySlateView {
  source: "feed" | "archive";
  /** The id we treat as a slate id for display. */
  slateId: string;
  generatedAt: string;
  date: string | null;
  picks: ArchivedPick[];
  notes?: string | null;
}

// ---------------------------------------------------------------------------
// Validators / normalisers.
// ---------------------------------------------------------------------------

function isFeedPick(x: unknown): x is DailyFeedPick {
  if (typeof x !== "object" || x === null) return false;
  const r = x as Record<string, unknown>;
  return (
    typeof r.id === "string" &&
    typeof r.sport === "string" &&
    typeof r.market_type === "string" &&
    typeof r.selection === "string" &&
    typeof r.grade === "string" &&
    typeof r.line === "object" && r.line !== null
  );
}

function isFeed(x: unknown): x is DailyFeed {
  if (typeof x !== "object" || x === null) return false;
  const r = x as Record<string, unknown>;
  return (
    typeof r.version === "number" &&
    typeof r.generated_at === "string" &&
    typeof r.date === "string" &&
    Array.isArray(r.picks) &&
    r.picks.every(isFeedPick)
  );
}

/** Normalise a feed pick into an ArchivedPick the existing UI can consume. */
function feedPickToArchivedPick(p: DailyFeedPick, idx: number): ArchivedPick {
  return {
    sport: p.sport,
    market_type: p.market_type,
    selection: p.selection,
    line: { odds: p.line?.odds ?? 0, number: p.line?.number ?? null },
    fair_prob: p.fair_prob ?? null,
    expected_value: null,
    edge: p.edge ?? null,
    kelly: p.kelly ?? null,
    grade: p.grade,
    realization: 0,
    game_id: p.game_id ?? null,
    event_time: p.event_time ?? null,
    decay_halflife_days: null,
    hfa_value: null,
    kelly_breakdown: null,
    metadata: p.notes ? { notes: p.notes } : {},
    pick_id: idx,
    slate_id: null,
    recorded_at: "",
  };
}

function archiveToView(slate: SlateDetail): DailySlateView {
  return {
    source: "archive",
    slateId: slate.slate_id ?? "archive",
    generatedAt: slate.generated_at ?? "",
    date: slate.generated_at ? slate.generated_at.slice(0, 10) : null,
    picks: slate.picks,
    notes: null,
  };
}

function feedToView(feed: DailyFeed): DailySlateView {
  return {
    source: "feed",
    slateId: `${feed.date}@${DAILY_FEED_VERSION}`,
    generatedAt: feed.generated_at,
    date: feed.date,
    picks: feed.picks.map(feedPickToArchivedPick),
    notes: feed.notes ?? null,
  };
}

// ---------------------------------------------------------------------------
// Loader.
// ---------------------------------------------------------------------------

/**
 * Load today's slate.
 *
 * Resolution order:
 *   1. Static JSON at <project>/public/data/daily/latest.json (run_daily.py).
 *   2. FastAPI archive — last persisted "daily_edge" slate.
 *
 * Returns null only when both sources fail.
 */
export async function loadDailyView(): Promise<{
  view: DailySlateView | null;
  error: string | null;
}> {
  let feedError: string | null = null;

  // 1. Try the static feed file first.
  try {
    const filePath = path.join(process.cwd(), DAILY_FEED_PATH);
    const raw = await fs.readFile(filePath, "utf-8");
    const parsed = JSON.parse(raw) as unknown;
    if (!isFeed(parsed)) {
      feedError = "feed file present but failed schema validation";
    } else if (parsed.version !== DAILY_FEED_VERSION) {
      feedError = `feed version mismatch: got ${parsed.version}, expected ${DAILY_FEED_VERSION}`;
    } else {
      return { view: feedToView(parsed), error: null };
    }
  } catch (e: unknown) {
    // ENOENT is normal when run_daily hasn't run yet — fall through silently.
    feedError = e instanceof Error ? e.message : "unknown feed error";
  }

  // 2. Fall back to the FastAPI archive.
  try {
    const slate = await api.latestSlate("daily_edge");
    if (slate) {
      return { view: archiveToView(slate), error: null };
    }
    return { view: null, error: feedError };
  } catch (e: unknown) {
    const apiErr = e instanceof Error ? e.message : "unknown api error";
    return {
      view: null,
      // Surface feed error first since the feed is the primary source.
      error: feedError ? `feed: ${feedError} · archive: ${apiErr}` : apiErr,
    };
  }
}
// Updated for v1 pipeline integration (mlb/ folder)
