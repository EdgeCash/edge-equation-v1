import fs from "node:fs/promises";
import path from "node:path";
import type { ArchivedPick } from "./types";

export async function loadDailyView() {
  try {
    const filePath = path.join(process.cwd(), "../public/data/mlb/todays_card.csv");
    const raw = await fs.readFile(filePath, "utf-8");
    
    const lines = raw.trim().split("\n");
    const picks: ArchivedPick[] = [];

    for (let i = 1; i < lines.length; i++) {
      const row = lines[i].split(",");
      if (row.length < 4) continue;

      picks.push({
        sport: "MLB",
        market_type: row[2] || "RUN_LINE",
        selection: row[3] || "",
        line: { odds: parseFloat(row[5] || "0"), number: null },
        fair_prob: null,
        expected_value: null,
        edge: row[6] ? row[6].toString() : null,
        kelly: row[7] ? row[7].toString() : null,
        grade: row[9] || "B",
        realization: 0,
        game_id: null,
        event_time: null,
        decay_halflife_days: null,
        hfa_value: null,
        kelly_breakdown: null,
        metadata: {},
        pick_id: i,
        slate_id: null,
        recorded_at: "",
      });
    }

    return {
      view: {
        source: "feed",
        slateId: "today",
        generatedAt: new Date().toISOString(),
        date: new Date().toISOString().slice(0, 10),
        picks: picks,
        notes: null,
      },
      error: null,
    };
  } catch (e) {
    return { 
      view: null, 
      error: `Failed to load todays_card.csv: ${e}` 
    };
  }
}
