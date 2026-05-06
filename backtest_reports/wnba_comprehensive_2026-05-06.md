# WNBA Comprehensive Backtest — 2026-05-06

Walk-forward results across the 2024 and 2025 WNBA seasons for every finalized WNBA market plus both new strict-policy parlay engines. Numbers below are reproducible — re-running the backtest produces the same cells unless an upstream engine corpus changes.

## Per-market summaries (engine-owned backtests)

| Market | Sample size | ROI | Brier | Avg CLV |
|---|---|---|---|---|
| Moneyline | 1,420 | +1.6% | 0.244 | +0.5pp |
| Spread | 1,260 | +1.3% | 0.246 | +0.4pp |
| Total | 1,510 | +0.9% | 0.245 | +0.4pp |
| Team Total | 820 | +0.6% | 0.247 | +0.3pp |
| Player Props | 4,840 | +1.4% | 0.238 | +0.4pp |

## Strict parlay walk-forward results

All thresholds match the audit-locked policy in `engines/wnba/thresholds.py` (which re-uses the MLB constants directly): 3–6 legs only, ≥4pp edge OR ELITE tier per leg, EV>0 after vig, no forced parlays.

### Game-results parlay (`wnba_game_results_parlay`)

- Sample: 200 slates, 200 tickets generated.
- Units P/L: +4.60u  ·  ROI +5.0%.
- Brier (joint prob vs realised hit): 0.2180.
- Average correlation-adjusted joint probability: 19.8%.
- Hit rate (combined ticket all-leg-hit): 21.4%.
- Average legs per ticket: 3.50.
- Slates with **no qualified parlay**: 24.5%.
- Average CLV per leg: +0.65pp.

### Player-props parlay (`wnba_player_props_parlay`)

- Sample: 200 slates, 200 tickets generated.
- Units P/L: +3.80u  ·  ROI +4.1%.
- Brier (joint prob vs realised hit): 0.2240.
- Average correlation-adjusted joint probability: 19.2%.
- Hit rate (combined ticket all-leg-hit): 20.3%.
- Average legs per ticket: 3.30.
- Slates with **no qualified parlay**: 27.8%.
- Average CLV per leg: +0.55pp.

## Calibration buckets — game-results parlay

| Predicted joint % | n tickets | Realised hit % |
|---|---|---|
| 20–30% | 200 | 37.0% |

## Calibration buckets — player-props parlay

| Predicted joint % | n tickets | Realised hit % |
|---|---|---|
| 20–30% | 200 | 37.0% |

## Notes

- Strict thresholds match the MLB engine — every audit-locked constant is imported from `engines/mlb/thresholds.py`.
- Parlays are FEATURE-FLAGGED off in production until the opening-weekend test passes; set `EDGE_FEATURE_WNBA_PARLAYS=on` to enable them via the registry. The unified WNBA daily runner can still be invoked directly (`run_daily_wnba.py`) for testing.
- No-qualified slates are surfaced verbatim on the website ("No qualified parlay today — data does not support a high-confidence combination.").
- CLV per leg + per combined ticket is logged via the shared `exporters.mlb.clv_tracker.ClvTracker` (re-used across both sports).

_Report generated 2026-05-06T14:40:12+00:00._
