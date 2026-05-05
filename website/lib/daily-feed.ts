// Schema + loader for the unified daily picks feed (v1 pipeline)
// Reads from public/data/mlb/mlb_daily.json produced by the new exporter

import fs from "node:fs/promises";
import path from "node:path";
import { api } from "./api";
import type { ArchivedPick, SlateDetail } from "./types";

export const DAILY_FEED_PATH = "../public/data/mlb/mlb_daily.json";
// Fixed for Vercel Root Directory = website/ + vercel.json

// ---------------------------------------------------------------------------
// Display type — what the daily-edge page actually renders.
// ---------------------------------------------------------------------------
export interface DailySlateView {
  source: "feed" | "archive";
  slateId: string;
  generatedAt: string;
  date: string | null;
  picks: ArchivedPick[];
  notes?: string | null;
}

// ---------------------------------------------------------------------------
// Loader for the new v1 pipeline JSON format
// ---------------------------------------------------------------------------
export async function loadDailyView(): Promise<{
  view: DailySlateView | null;
  error: string | null;
}> {
  try {
    const filePath = path.join(process.cwd(), DAILY_FEED_PATH);
    const raw = await fs.readFile(filePath, "utf-8");
    const data = JSON.parse(raw);

    // New pipeline format with .tabs
    if (data.tabs) {
      const allPicks: ArchivedPick[] = [];

      Object.keys(data.tabs).forEach((marketKey) => {
        const tab = data.tabs[marketKey];
        if (tab && tab.projections && Array.isArray(tab.projections)) {
          tab.projections.forEach((p: any, idx: number) => {
            allPicks.push({
              sport: "MLB",
              market_type: marketKey.toUpperCase().replace(/_/g, " "),
              selection: p.pick || p.selection || p.team || "",
              line: { 
                odds: Number(p.market_odds_american || p.market_odds_dec || 0), 
                number: p.line || null 
              },
              fair_prob: p.model_prob != null ? String(p.model_prob) : null,
              expected_value: null,
              edge: p.edge_pct != null ? String(p.edge_pct) : null,
              kelly: p.kelly_pct != null ? String(p.kelly_pct) : null,
              grade: p.grade || (p.kelly_advice?.includes("u") ? "A" : "B"),
              realization: 0,
              game_id: null,
              event_time: null,
              decay_halflife_days: null,
              hfa_value: null,
              kelly_breakdown: null,
              metadata: {},
              pick_id: idx,
              slate_id: null,
              recorded_at: "",
            });
          });
        }
      });

      return {
        view: {
          source: "feed",
          slateId: data.today || "today",
          generatedAt: data.generated_at || new Date().toISOString(),
          date: data.today || new Date().toISOString().slice(0, 10),
          picks: allPicks,
          notes: null,
        },
        error: null,
      };
    }

    // Fallback if old format is somehow still there
    return { 
      view: null, 
      error: "New pipeline format detected but no tabs found" 
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return { 
      view: null, 
      error: `Failed to load mlb_daily.json: ${msg}` 
    };
  }
}
