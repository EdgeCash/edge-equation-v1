import fs from "node:fs/promises";
import path from "node:path";
import type { ArchivedPick } from "./types";

export interface DailySlateView {
  source: string;
  slateId: string;
  generatedAt: string;
  date: string;
  picks: ArchivedPick[];
  notes: string | null;
}

// Edge-percent → letter grade. Mirrors src/edge_equation/math/scoring.py
// thresholds restated in percent (the CSV stores edge as 4.5, not 0.045).
function gradeFromEdgePct(edgePct: number): "A+" | "A" | "B" | "C" | "D" | "F" {
  if (edgePct >= 8) return "A+";
  if (edgePct >= 5) return "A";
  if (edgePct >= 3) return "B";
  if (edgePct >= 0) return "C";
  if (edgePct >= -3) return "D";
  return "F";
}

function parseCsvRow(line: string): string[] {
  // todays_card.csv from the orchestrator does not emit quoted fields
  // with embedded commas, so a plain split is safe. If the schema ever
  // adds quoted fields, swap this for a real CSV parser.
  return line.split(",");
}

export async function loadDailyView(): Promise<{
  view: DailySlateView | null;
  error: string | null;
}> {
  // The orchestrator writes into <repo>/website/public/data/mlb/. With
  // Vercel's Root Directory = website/, process.cwd() at request time
  // is the website/ root, so we read from public/data/mlb/ directly
  // with no ".." escape (which doesn't survive Vercel's serverless
  // bundling).
  const filePath = path.join(
    process.cwd(),
    "public",
    "data",
    "mlb",
    "todays_card.csv",
  );
  let raw: string;
  try {
    raw = await fs.readFile(filePath, "utf-8");
  } catch (e) {
    return {
      view: null,
      error: `Failed to read ${filePath}: ${e instanceof Error ? e.message : String(e)}`,
    };
  }

  const lines = raw.trim().split("\n");
  if (lines.length === 0) {
    return { view: null, error: "todays_card.csv is empty" };
  }

  // Header-driven parsing — never trust positional column indices, the
  // orchestrator's column order has shifted historically. Resolve each
  // column by name up front.
  const header = parseCsvRow(lines[0]);
  const col = (name: string): number => header.indexOf(name);
  const colSection = col("section");
  const colDate = col("date");
  const colMatchup = col("matchup");
  const colBetType = col("bet_type");
  const colPick = col("pick");
  const colModelProb = col("model_prob");
  const colMarketDec = col("market_odds_dec");
  const colMarketAm = col("market_odds_american");
  const colEdgePct = col("edge_pct");
  const colKellyPct = col("kelly_pct");
  const colKellyAdvice = col("kelly_advice");

  const picks: ArchivedPick[] = [];
  let pickId = 0;
  let latestDate = "";

  for (let i = 1; i < lines.length; i++) {
    const row = parseCsvRow(lines[i]);
    if (row.length < 5) continue;

    // todays_card.csv mixes section=projection (today's actionable
    // picks) with section=backfill (recent settled picks). The
    // daily-edge page is for today's plays only; filter out backfill.
    const section = colSection >= 0 ? row[colSection] : "projection";
    if (section !== "projection") continue;

    const date = colDate >= 0 ? row[colDate] : "";
    if (date && date > latestDate) latestDate = date;

    const betType = (colBetType >= 0 ? row[colBetType] : "").toUpperCase();
    const matchup = colMatchup >= 0 ? row[colMatchup] : "";
    const pick = colPick >= 0 ? row[colPick] : "";

    const oddsAmStr = colMarketAm >= 0 ? row[colMarketAm] : "";
    const oddsAm = parseFloat(oddsAmStr);

    const edgePctStr = colEdgePct >= 0 ? row[colEdgePct] : "";
    const edgePct = parseFloat(edgePctStr);

    const kellyPctStr = colKellyPct >= 0 ? row[colKellyPct] : "";
    const modelProbStr = colModelProb >= 0 ? row[colModelProb] : "";
    const kellyAdvice = colKellyAdvice >= 0 ? row[colKellyAdvice] : "";
    const marketDec = colMarketDec >= 0 ? row[colMarketDec] : "";

    const grade: "A+" | "A" | "B" | "C" | "D" | "F" =
      Number.isFinite(edgePct) ? gradeFromEdgePct(edgePct) : "C";

    picks.push({
      sport: "MLB",
      market_type: betType,
      selection: matchup ? `${pick} · ${matchup}` : pick,
      line: {
        odds: Number.isFinite(oddsAm) ? oddsAm : 0,
        number: null,
      },
      fair_prob: modelProbStr || null,
      expected_value: null,
      edge: Number.isFinite(edgePct) ? edgePctStr : null,
      kelly: kellyPctStr || null,
      grade,
      realization: 0,
      game_id: null,
      event_time: null,
      decay_halflife_days: null,
      hfa_value: null,
      kelly_breakdown: null,
      metadata: { kelly_advice: kellyAdvice, market_odds_dec: marketDec },
      pick_id: ++pickId,
      slate_id: null,
      recorded_at: date,
    });
  }

  if (picks.length === 0) {
    return {
      view: null,
      error:
        "todays_card.csv has no projection rows. The market gate may " +
        "have rejected every pick today, or the daily orchestrator " +
        "has not yet written today's slate.",
    };
  }

  return {
    view: {
      source: "todays_card.csv",
      slateId: latestDate || "today",
      generatedAt: new Date().toISOString(),
      date: latestDate || new Date().toISOString().slice(0, 10),
      picks,
      notes: null,
    },
    error: null,
  };
}
