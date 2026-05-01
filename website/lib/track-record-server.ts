// Server-only loader for the track-record JSON files. **Never import
// this from a React component or any module pulled into the client
// bundle** — the `fs` / `path` imports below would crash the
// build. Only `getStaticProps` / `getServerSideProps` / API routes
// should reach for this file.
//
// The client-safe types and helpers live in `track-record.ts`.

import fs from "fs/promises";
import path from "path";

import type { TrackRecordView } from "./track-record";


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
