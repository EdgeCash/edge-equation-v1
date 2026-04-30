# NCAAF Engine — Architecture & Phasing

Mirrors the NFL engine layout, with college-football-specific
adjustments documented below.

## Folder layout

```
engines/ncaaf/
├── __init__.py
├── README.md               # this file
├── config.py
├── markets.py
├── daily.py
├── ledger.py
├── features/
├── models/
├── calibration/
├── output/
└── source/
```

Identical structure to `engines/nfl/` so a contributor crossing
between leagues doesn't relearn the layout.

## Key differences from NFL

| Concern | NFL | NCAAF |
|---|---|---|
| **Talent gap** | small — every team starts NFL caliber | huge — top-25 vs unranked = 30+ point spread |
| **Sample size** | 1 game / week | 1 game / week, but 12-game season vs 17 |
| **Conference tiers** | none | SEC / Big Ten / Big 12 / ACC vs G5 vs FCS |
| **Recruit ratings** | n/a | 247 / Rivals composite as preseason talent prior |
| **Transfer portal** | low impact | huge — QB transfers swing season-long projections |
| **Schedule structure** | balanced | non-conference tune-up games distort early ratings |
| **Bowl games** | n/a in regular season | separate motivation regime + opt-outs |
| **Player props** | full inventory | narrower — books only post on marquee matchups |
| **Spread distribution** | tight, key-number heavy | wider tail, key-numbers still cluster |
| **Weather coverage** | every venue tracked | many smaller venues lack good coverage |

## Key differences from MLB (high-level)

Same as NFL — the NFL README's table covers football-vs-baseball
generalities (sample size, game script, QB dominance, rest variance,
spread-as-dominant-market, market-efficiency-medium-vs-sharp). The
NCAAF-specific deltas are documented above.

## Tier policy

Same engine-wide ladder (ELITE / STRONG / MODERATE / LEAN /
NO_PLAY) on the edge basis. Realistic edges are slightly more
common than NFL because:

1. Books focus their sharpest pricing on top-25 matchups — the
   second-tier slate (unranked vs unranked) has thicker mispricing.
2. Public-betting heuristics ("always pick the favorite", "fade
   bad teams") create exploitable line moves.
3. Weather impact is often under-priced on smaller-conference
   matchups (books concentrate weather analysts on Saturday's
   biggest games).

That said — the wider variance also means a 60% projection in
NCAAF carries more noise than a 60% projection in NFL. Don't lower
the edge thresholds.

## Phasing plan

Same F-1 → F-5 phasing as NFL. F-1 (this PR) ships skeleton only.

### Phase F-1 (this PR) — skeleton

* Folder structure + READMEs.
* `NCAAFConfig` + `ProjectionKnobs`.
* `markets.py` with shared football vocabulary + Odds API key
  mapping.
* Stubbed `daily.py` returning an empty card.
* `ledger.py` DDL.
* Empty `features/`, `models/`, `calibration/`, `source/`,
  `output/` packages.

### Phase F-1.5 (shipped) — data foundation

* Resumable backfill orchestrator at
  `engines/football_core/data/backfill_ncaaf.py`. Pulls games / plays
  / lines from the College Football Data API (free tier; 1000
  req/mo via `CFBD_API_KEY`) and per-game weather from Open-Meteo
  archive. Optional Odds API historical lines behind
  `--include-historical-odds` (paid tier).
* Per-week play-by-play checkpointing — each `(season, week)` pull
  is its own `plays_w<n>` checkpoint, so a partial-week failure only
  retries that one week.
* CFBD `/lines` always pulled (free) — sparse compared to The Odds
  API but a good baseline.
* Shares the football DuckDB schema with NFL (sport-discriminated
  rows in the same tables).
* Run a season: `python -m edge_equation.engines.football_core.data.backfill_ncaaf --season 2025`.

### Phase F-2 — data pipeline

* `source/odds_fetcher.py` for `americanfootball_ncaaf` (live).
* `source/schedule.py` for current-week pulls (historical already in
  DuckDB after F-1.5).
* `source/recruit_ratings.py` annual composite ingest.
* `source/transfer_portal.py` weekly tracker.
* `source/storage.py` thin reader over the F-1.5 DuckDB tables.
* `features/team_rates.py` with conference-tier-aware Bayesian prior,
  computed off the F-1.5 plays corpus.

### Phase F-3 — projection + edge

* `models/projection.py` with conference-tier blend.
* `calibration/spread_calibration.py` with NCAAF key-number lookup
  (tail-heavier than NFL's).
* `output/payload.py` factory + email/api adapters.

### Phase F-4 — training + R2

* `models/model_training.py` — XGBoost + walk-forward on 4 college
  seasons. Recruit ratings folded in as a preseason prior.
* Sanity gate before R2 promotion.

### Phase F-5 — daily integration + workflows

* Wire `build_ncaaf_card` into the unified `run_daily` entrypoint.
* New `ncaaf-saturday-email` cron workflow (Saturday morning CT).

## Open design questions (for follow-up)

1. **Transfer-portal handling** — when does a QB transfer's impact
   show up? Game 1, or do we keep the prior heavy until the
   transferred QB has ~3 starts on the new roster?
2. **FBS-vs-FCS body-bag games** — should we project them at all?
   Probably not — books typically take them off the board for
   spread bets, and the edge math on a -45 spread is fundamentally
   noise.
3. **Bowl game motivation** — opt-outs and lame-duck coaching
   situations require a separate calibration regime. Build it in
   F-3 or punt to F-5?
4. **Weekly sharp moves** — Tuesday opening lines vs Saturday
   closing lines move dramatically in NCAAF (more than NFL).
   Track line history in the data layer to surface RLM as a
   feature?
