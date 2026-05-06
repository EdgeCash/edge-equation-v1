/**
 * Cross-sport player + team search index.
 *
 * Built once at request time from the unified daily feed + the per-sport
 * legacy data files. Each entry exposes a deterministic `id` (slugified
 * name) so the profile pages at /player/[sport]/[id] and
 * /team/[sport]/[id] can resolve back to the underlying pick rows.
 *
 * Search scoring lives in `lib/fuzzy.ts` — exact / prefix / substring /
 * Levenshtein-typo / subsequence ladder, no external dependency.
 */

import {
  DailyFeed,
  FeedPick,
  SPORTS,
  SportKey,
  picksForSport,
} from "./feed";
import { bestFuzzyScore } from "./fuzzy";


export type SearchEntryKind = "player" | "team";


export interface SearchEntry {
  id: string;
  sport: SportKey;
  kind: SearchEntryKind;
  display: string;
  detail: string;
}


/** Lowercase-alphanumeric slug. Stable across renders. */
export function slugify(name: string): string {
  return (name || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}


/** Best-effort split: returns `{ player, team, opponent }` parsed from a
 * pick's `selection` string. */
export function parseSelection(pick: FeedPick): {
  player: string | null;
  team: string | null;
  opponent: string | null;
} {
  const sel = String(pick.selection || "");
  const market = String(pick.market_type || "");

  // Player props: "Aaron Judge · Home Runs Over 0.5" or
  // "Patrick Mahomes · Passing Yards Over 275.5".
  if (market.startsWith("PLAYER_PROP_") || sel.includes(" · ")) {
    const [first, ...rest] = sel.split("·").map((s) => s.trim());
    if (first && rest.length > 0) {
      return { player: first, team: null, opponent: null };
    }
  }

  // NRFI / YRFI: "NRFI · NYY @ BOS"
  if (sel.includes(" @ ")) {
    const m = sel.match(/([A-Z]{2,4})\s*@\s*([A-Z]{2,4})/);
    if (m) return { player: null, team: m[2], opponent: m[1] };
  }

  // Football / WNBA team picks: "NYG · Spread -3.5", "LAS Team Total Over 80"
  const teamCode = sel.match(/^([A-Z]{2,4})\b/);
  if (teamCode) {
    return { player: null, team: teamCode[1], opponent: null };
  }
  return { player: null, team: null, opponent: null };
}


export function buildSearchIndex(feed: DailyFeed | null): SearchEntry[] {
  if (!feed) return [];
  const entries: SearchEntry[] = [];
  const seen = new Set<string>();

  for (const sport of SPORTS) {
    const picks = picksForSport(feed, sport);
    for (const p of picks) {
      const { player, team } = parseSelection(p);
      if (player) {
        const id = slugify(player);
        const key = `${sport}:player:${id}`;
        if (!seen.has(key)) {
          seen.add(key);
          entries.push({
            id, sport, kind: "player",
            display: player,
            detail: `${sport.toUpperCase()} · ${formatMarketHint(p.market_type)}`,
          });
        }
      }
      if (team) {
        const id = slugify(team);
        const key = `${sport}:team:${id}`;
        if (!seen.has(key)) {
          seen.add(key);
          entries.push({
            id, sport, kind: "team",
            display: team,
            detail: `${sport.toUpperCase()} team`,
          });
        }
      }
    }
  }

  // Stable alphabetical sort so consecutive renders return the same
  // dropdown order.
  entries.sort((a, b) => a.display.localeCompare(b.display));
  return entries;
}


/** Fuzzy + typo-tolerant search.
 *
 * Mirrors the audit's "novice typing 'judge' reaches Aaron Judge AND
 * a typo like 'juge' still resolves" requirement. Scoring lives in
 * `lib/fuzzy.ts`. We score every entry's `display`, `id`, and
 * `sport` fields, take the best, and return the top-N sorted by
 * score descending.
 *
 * Empty query returns a stable alphabetical slice (parity with the
 * old substring-only behaviour the navbar dropdown depends on).
 */
export function searchEntries(
  index: SearchEntry[], query: string, limit = 12,
): SearchEntry[] {
  const q = query.trim();
  if (!q) return index.slice(0, limit);

  const scored: Array<{ entry: SearchEntry; score: number }> = [];
  for (const entry of index) {
    const m = bestFuzzyScore(q, [entry.display, entry.id, entry.sport]);
    if (m.score > 0) {
      scored.push({ entry, score: m.score });
    }
  }
  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.entry.display.localeCompare(b.entry.display);
  });
  return scored.slice(0, limit).map((s) => s.entry);
}


function formatMarketHint(market: string): string {
  const m = market.replace(/^PLAYER_PROP_/, "").replace(/_/g, " ");
  return m.length ? m : "pick";
}
