# `track-record/` — public ledger feed

These three JSON files are **generated** by the engine workflow
[`website-publish-track-record.yml`](../../../.github/workflows/website-publish-track-record.yml).
Do not hand-edit them.

| File | Source | What it drives |
|---|---|---|
| `ledger.json` | Every settled (LEAN-and-above) pick across all four engines | The main ledger table on `/track-record` |
| `summary.json` | Per `(engine, season, tier)` running record | The four tier-summary cards at the top of the page |
| `by-day.json` | Per-day W/L/Push roll-up | Future sparkline / recent-form widget |

The exporter that produces them lives at
`src/edge_equation/engines/website/build_track_record.py`. The page
that consumes them lives at `website/pages/track-record.tsx`.

## Why this is a flat JSON dump and not an API

Vercel rebuilds the static site on every git push. The exporter
commits the JSON files into the repo via the workflow, which triggers
a Vercel rebuild and the page re-renders against fresh data. No
runtime API, no auth, no rate limits — just a static, public,
auditable ledger.

If we ever need richer queries (per-game detail pages, time-series
charts), we'd promote `ledger.json` to a real API endpoint. For the
v1 free public release the static file is enough.

## Empty / missing files

If the JSON files don't exist yet (fresh checkout, exporter hasn't
run), `loadTrackRecord()` in `lib/track-record.ts` returns an empty
placeholder bundle and the page renders a "no data yet" state. The
build will not error.
