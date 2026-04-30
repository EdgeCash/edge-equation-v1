# NFL Engine — Architecture & Phasing

Mirrors the MLB pattern (`engines/nrfi/`, `engines/props_prizepicks/`,
`engines/full_game/`) so operators and contributors carry one mental
model across sports.

## Folder layout

```
engines/nfl/
├── __init__.py             # public API
├── README.md               # this file
├── config.py               # NFLConfig + ProjectionKnobs
├── markets.py              # canonical → Odds API mapping
├── daily.py                # build_nfl_card orchestrator + top-N renderer
├── ledger.py               # per-tier YTD ledger
├── features/               # per-team / per-player feature builders
├── models/                 # projection + inference + training
├── calibration/            # spread / total / prop-prob calibration
├── output/                 # canonical NFLOutput payload + adapters
└── source/                 # odds fetcher + schedule + injuries + weather + storage
```

This deliberately mirrors `engines/full_game/` so a contributor who
knows MLB can navigate NFL without relearning the layout.

## Key differences from MLB

| Concern | MLB | NFL |
|---|---|---|
| **Sample size per team** | 6-7 games / week | 1 game / week |
| **Rolling-rate window** | 60 days (~40 games) | 6 games (~6 weeks) |
| **Bayesian shrinkage** | `prior_weight_pa=80` | `prior_weight_games=6` (heavier) |
| **Game script effects** | minor (Statcast outcomes are play-by-play) | huge — early blowouts produce garbage-time stats. Filter by win-prob ≤ 0.85 |
| **Single-position dominance** | none | QB1 → QB2 swings the line 4-7 points |
| **Rest variance** | irrelevant (daily) | Thu / Sun / Mon → ~1.5-point swings |
| **Weather impact** | first-inning runs only | full-game totals; wind ≥ 15 mph or temp ≤ 32 °F move totals 0.3-1.0 points |
| **Home-field advantage** | tiny (~0.04 RPG) | meaningful (~2-2.5 points) |
| **Spread structure** | run-line is a niche market | spread is THE market |
| **Key numbers** | n/a | -3 / +3 / -7 / +7 due to FG/TD discreteness |
| **Market efficiency** | medium | among the sharpest in sports |
| **Player-prop vocabulary** | HR / Hits / Total Bases / RBI / K | Pass Yds / Rush Yds / Rec Yds / Anytime TD / Longest Rec |

## Tier policy (same as engine-wide)

NFL markets use the **edge ladder** (model_p − vig-adjusted market_p),
not raw probability. Per the audit:

* **ELITE** — ≥ 8 pp edge (Electric Blue)
* **STRONG** — 5-8 pp (Deep Green)
* **MODERATE** — 3-5 pp (Light Green)
* **LEAN** — 1-3 pp (Yellow, content-only)
* **NO_PLAY** — < 1 pp (Orange, filtered)

Realistic edges show up less often than in MLB. We'd rather show 3
ELITE plays a season than chase 30 noisy STRONG plays. The strict
floor is intentional.

## Phasing plan

### Phase F-1 (this PR) — skeleton

* Folder structure + READMEs.
* `NFLConfig` + `ProjectionKnobs`.
* `markets.py` with shared football vocabulary + Odds API key
  mapping.
* Stubbed `daily.py` returning an empty card.
* `ledger.py` DDL.
* Empty `features/`, `models/`, `calibration/`, `source/`,
  `output/` packages.

### Phase F-2 — data pipeline

* `source/odds_fetcher.py` — per-event Odds API for NFL.
* `source/schedule.py` — nflverse / sportradar pull.
* `source/storage.py` — DuckDB tables.
* `source/injuries.py` — practice-report scraper.
* `features/team_rates.py` — rolling per-team rates with garbage-time
  filtering.
* `features/qb_rates.py` — per-QB rolling rates + injury status.

### Phase F-3 — projection + edge

* `models/projection.py` — per-team Skellam-shifted Poisson, with
  QB / rest / weather adjustments stacked.
* `calibration/spread_calibration.py` — key-number-aware isotonic.
* `output/payload.py` factory + email/api adapters.
* `daily.build_nfl_card` returns real picks.

### Phase F-4 — training + R2

* `models/model_training.py` — XGBoost team-strength + walk-forward
  on 4 NFL seasons.
* `models/inference.py` — bundle loader + R2 fallback (mirror NRFI).
* Sanity gate before promoting to R2.

### Phase F-5 — daily integration + workflows

* Wire `build_nfl_card` into the unified `run_daily` entrypoint.
* New `nfl-daily-email` cron workflow (Saturday/Sunday-AM CT).
* Backfill historical odds + actuals for the trainer.

## Open design questions (for follow-up)

1. **How do we calibrate the QB-out adjustment?** The default in
   `football_core/qb_adjustments.py` is league-average (-5 points
   for OUT). Per-team overrides need a depth-chart-aware model;
   not landing in F-1.
2. **Public-betting fade** — NFL has well-documented "fade the
   public" patterns (heavy public action on favorites → reverse
   line movement). Worth modeling? Probably yes; not in F-1.
3. **Home-field advantage by venue** — Lambeau-in-December and
   Mile-High-altitude are different from a generic dome HFA.
   Hardcode by venue? Bayesian-blend per-venue? F-3 territory.
4. **Spread distribution shape** — NFL margins cluster heavily at
   key numbers. Naive Poisson-Skellam under-prices the -3 / +3
   point exactly. Need a discrete-margin lookup overlay; F-3.
