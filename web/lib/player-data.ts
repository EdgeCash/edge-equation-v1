/**
 * Player game logs + today's-context loaders.
 *
 * Both loaders read from per-sport JSON files the engines can write
 * later — defining the shape here gives the website a real renderer
 * without requiring any engine changes today. Profile pages render
 * an honest "Limited data" panel when the files don't exist yet,
 * matching the same rule every other section of the site already
 * follows.
 *
 * Schema contract (engines populate these on a future PR):
 *
 *   public/data/<sport>/player_logs/<slug>.json
 *     { "player": "Aaron Judge", "rows": [
 *         { "date": "2026-04-29", "opponent": "BOS", "is_home": true,
 *           "result": "W", "stats": { "AB": 4, "H": 2, "HR": 1,
 *           "RBI": 3, "BB": 1, "K": 1 } }, ...
 *     ] }
 *
 *   public/data/<sport>/context_today/<slug>.json
 *     { "player": "Aaron Judge", "as_of": "2026-05-06T13:32Z",
 *       "items": [
 *         { "label": "Lineup spot",  "value": "2nd, DH" },
 *         { "label": "Opponent",     "value": "vs BOS · Crawford (LHP)" },
 *         { "label": "Weather",      "value": "75°F · 8mph out to LF" },
 *         { "label": "Injury",       "value": "Active" }, ...
 *       ] }
 *
 * Tolerant loaders: missing fields, missing files, malformed JSON
 * all return null + the page renders a "Limited data" panel.
 */

import fs from "node:fs/promises";
import path from "node:path";

import { SportKey } from "./feed";


export interface PlayerGameLogRow {
  date: string;
  opponent: string;
  is_home: boolean;
  result: "W" | "L" | "T" | string | null;
  stats: Record<string, number | string | null>;
}


export interface PlayerGameLog {
  player: string;
  rows: PlayerGameLogRow[];
}


export interface PlayerContextItem {
  label: string;
  value: string;
}


export interface PlayerContextSnapshot {
  player: string;
  as_of: string | null;
  items: PlayerContextItem[];
}


export async function loadPlayerGameLog(
  sport: SportKey, slug: string,
): Promise<PlayerGameLog | null> {
  const file = path.join(
    process.cwd(), "public", "data",
    sport, "player_logs", `${slug}.json`,
  );
  try {
    const raw = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(raw) as Partial<PlayerGameLog>;
    if (!parsed || !Array.isArray(parsed.rows)) return null;
    return {
      player: String(parsed.player ?? ""),
      rows: parsed.rows.map((r) => ({
        date: String(r.date ?? ""),
        opponent: String(r.opponent ?? ""),
        is_home: !!r.is_home,
        result: r.result ?? null,
        stats: (r.stats ?? {}) as Record<string, number | string | null>,
      })),
    };
  } catch {
    return null;
  }
}


export async function loadPlayerContextToday(
  sport: SportKey, slug: string,
): Promise<PlayerContextSnapshot | null> {
  const file = path.join(
    process.cwd(), "public", "data",
    sport, "context_today", `${slug}.json`,
  );
  try {
    const raw = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(raw) as Partial<PlayerContextSnapshot>;
    if (!parsed || !Array.isArray(parsed.items)) return null;
    return {
      player: String(parsed.player ?? ""),
      as_of: parsed.as_of ?? null,
      items: parsed.items
        .filter((it) => it && typeof it.label === "string"
          && typeof it.value === "string")
        .map((it) => ({ label: it.label, value: it.value })),
    };
  } catch {
    return null;
  }
}
