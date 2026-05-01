# Daily Picks Feed

This directory holds the static JSON feed that powers the Daily Edge page.

## Contract

- **Path**: `website/public/data/daily/latest.json`
- **Producer**: `python -m edge_equation.run_daily` (or any pipeline writing the same shape)
- **Consumer**: `pages/daily-edge.tsx` via `lib/daily-feed.ts`
- **Fallback**: the FastAPI archive (`/archive/slates/latest?card_type=daily_edge`) is used automatically when this file is missing or fails schema validation.

## Schema (version 1)

```jsonc
{
  "version": 1,
  "generated_at": "2026-04-30T17:00:00Z",   // ISO datetime, UTC
  "date": "2026-04-30",                     // YYYY-MM-DD slate date
  "source": "run_daily.py",
  "notes": "optional commentary shown under the slate summary",
  "picks": [
    {
      "id": "778899-NRFI",                  // unique within slate
      "sport": "MLB",
      "market_type": "NRFI",                // see classifier table below
      "selection": "NRFI · NYY @ BOS",
      "line": { "number": null, "odds": -125 },
      "fair_prob": "0.6280",                // decimal-as-string for precision
      "edge": "0.0480",
      "kelly": "0.0090",
      "grade": "A+",                        // A+ | A | B | C | D | F
      "tier": null,                         // optional explicit ConvictionTier
      "notes": "K. Smith vs. R. Jones — both pitchers below 0.18 1st-inning ER…",
      "event_time": "2026-04-30T23:10:00Z",
      "game_id": "778899"
    }
  ]
}
```

### Field notes

- `picks[].grade` drives the conviction tier via `tierFromGrade` unless `picks[].tier` is provided. Tier values are `ELITE`, `STRONG`, `MODERATE`, `LEAN`, `NO_PLAY`. Coloring is tier-only (post 2026-05-01) — Red is reserved for `NO_PLAY`.
- All decimal-valued fields (`fair_prob`, `edge`, `kelly`) are JSON strings, not numbers. The website preserves them as strings until display.
- `line.number` is the line value (run total, prop number, spread, etc.) or `null`. `line.odds` is American odds.

## Market type → group mapping

The Daily Edge page groups picks into First Inning / Props / Full Game / Other based on `market_type`. The classifier in `pages/daily-edge.tsx::classify` looks for these substrings (case-insensitive, after `toUpperCase`):

| Group         | Match                                       |
|---------------|---------------------------------------------|
| First Inning  | `NRFI`, `YRFI`, `*FIRST_INNING*`            |
| Props         | `*PLAYER*`, `*PROP*`, `*HITS*`, `*HRS*`, `*STRIKEOUT*`, `*RBI*` |
| Full Game     | `MONEYLINE`, `TOTAL`, `RUN_LINE`, `SPREAD`, `*FULL_GAME*` |
| Other         | anything else                               |

If you're adding a new market type, prefer naming it so it lands in one of the existing groups, or extend the classifier.

## Versioning

If the schema needs to change in a backwards-incompatible way, bump `version`. The loader will refuse a non-matching version and fall back to the archive — the website won't render stale or wrong data.
