/**
 * Cross-sport search API.
 *
 * GET /api/search        → full index (used to hydrate the navbar
 *                            search box once on first interaction).
 * GET /api/search?q=...  → top-12 entries that substring-match the
 *                            query.
 *
 * The index is rebuilt per request from the unified daily feed —
 * cheap given the file is already cached on the Next.js side, and
 * keeps the search results fresh across daily-card re-runs without
 * a deploy.
 */

import { NextRequest, NextResponse } from "next/server";

import { getDailyFeed } from "../../../lib/feed";
import { buildSearchIndex, searchEntries } from "../../../lib/search-index";


export const dynamic = "force-dynamic";


export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const q = (searchParams.get("q") ?? "").trim();
  const feed = await getDailyFeed();
  const index = buildSearchIndex(feed);
  const results = q ? searchEntries(index, q, 12) : index.slice(0, 24);
  return NextResponse.json({
    q,
    n_total: index.length,
    n_results: results.length,
    results,
  });
}
