# football_core — shared utilities for `nfl/` and `ncaaf/`

Sport-specific layer between the engine-wide `tiering` module (which is
sport-agnostic) and each league's per-week pipeline. Holds vocabulary
and helpers that BOTH football leagues need but neither baseball nor
basketball would use.

## What lives here

| Module | Purpose |
|---|---|
| `markets.py` | Canonical market vocabulary — Spread / Total / ML / Player Props / Alternate Lines. Each league's `markets.py` maps these to the Odds API per-league keys. |
| `weather.py` | Outdoor venue classification + impact scoring for wind / temp / precip. Dome / retractable handling. |
| `rest_days.py` | Days-since-last-game classifier. NFL has Thu/Sun/Mon variance; NCAAF mostly Saturdays with Friday and weeknight outliers. |
| `qb_adjustments.py` | Injury-status → expected-points-delta lookup. The single highest-leverage feature for football projection. |

## What does NOT live here

* **Per-league projection** — NFL and NCAAF projections diverge enough
  (talent gap, conference tiers, recruit ratings) that they get their
  own `projection.py` per sport.
* **Per-league market keys** — The Odds API keys for spreads/totals
  are sport-prefixed (`spreads`, `spreads_ncaaf` doesn't exist; instead
  the sport context is set via the `sport_key` URL param). Each league's
  `markets.py` owns its own key mapping.
* **Schedule / source / feature builder** — sport-specific data layer
  lives under `nfl/source/` or `ncaaf/source/`.

## Tier policy across football

Same engine-wide `Tier` enum (ELITE / STRONG / MODERATE / LEAN /
NO_PLAY) applies. Football markets use the **edge ladder** (not the
NRFI-style raw-probability ladder) since spreads and totals are
asymmetric — a 60% prediction on a -250 favorite is a fade, not a
play.

| Tier | Edge threshold | Color band | Notes |
|---|---|---|---|
| ELITE | ≥ 8 pp | Electric Blue | Rare; usually weather-driven or QB-injury-driven mispricing |
| STRONG | 5 - 8 pp | Deep Green | The bread-and-butter conviction band |
| MODERATE | 3 - 5 pp | Light Green | Quality plays; smaller stake |
| LEAN | 1 - 3 pp | Yellow | Content-only per the audit |
| NO_PLAY | < 1 pp | Orange | Filtered out |

NFL markets are tighter than MLB (more sharp money, less fade-the-
public daylight) so realistic edges show up less often. The NCAAF
market is looser at the edges of the schedule (small-conference
matchups, late-September tune-ups) where the operator can find
genuine 8+ pp edges before the Vegas line moves.

## Key differences from MLB (high-level)

These are the things the per-league READMEs explore in depth:

1. **Sample size** — one game per team per week vs 6-7 games per week
   for MLB. Bayesian shrinkage matters more; rolling-rate windows
   measured in games (last 4-6) rather than days.
2. **Game script** — early blowouts produce garbage-time stats that
   distort per-team rate calculations. Filtering by win-probability /
   EPA at the time of the play is necessary to get clean rates.
3. **QB dominance** — single-position swings the line 4-7 points.
   Real-time injury feeds + depth-chart awareness are first-class
   features.
4. **Rest variance** — Thu/Sun/Mon games (NFL) and Friday/Saturday
   (NCAAF) create rest-day variance that meaningfully affects
   performance.
5. **Spread is the dominant market** — totals and player props get
   built off the same projected score line, but the spread is what
   the operator's bet flow lives on.
6. **Market efficiency** — NFL is among the sharpest markets in
   sports. Edge thresholds are conservative; we'd rather show 3 ELITE
   plays a season than chase 30 noisy STRONG plays.

## Status

**Phase F-1 skeleton.** Modules are stubbed with placeholder logic
and educated-guess coefficients. Real magnitudes need a backtest
harness with at least 4 NFL seasons of game-script data + the same
for NCAAF. Track that work under `claude/football-*` PRs.
