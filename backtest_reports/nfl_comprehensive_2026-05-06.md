# NFL Comprehensive Backtest — 2026-05-06

Walk-forward results across the 2022, 2023, 2024 seasons for every finalized NFL market plus both new strict-policy parlay engines. Numbers below are reproducible — re-running the backtest produces the same cells unless an upstream engine corpus changes.

## Per-market summaries (engine-owned backtests)

| Market | Sample size | ROI | Brier | Avg CLV |
|---|---|---|---|---|
| Moneyline | 1,090 | +1.2% | 0.244 | +0.4pp |
| Spread | 2,260 | +1.7% | 0.241 | +0.5pp |
| Total | 1,810 | +1.1% | 0.243 | +0.4pp |
| Team Total | 920 | +0.8% | 0.246 | +0.3pp |
| First Half / 1Q | 1,460 | +0.9% | 0.245 | +0.4pp |
| Player Props | 6,210 | +1.5% | 0.236 | +0.5pp |

## Strict parlay walk-forward results

All thresholds match the audit-locked policy in the shared football thresholds module (which re-uses the MLB constants directly): 3–6 legs only, ≥4pp edge OR ELITE tier per leg, EV>0 after vig, no forced parlays.

### Game-results parlay

- Sample: 240 slates, 240 tickets generated.
- Units P/L: +6.40u  ·  ROI +4.6%.
- Brier (joint prob vs realised hit): 0.2190.
- Average correlation-adjusted joint probability: 19.6%.
- Hit rate (combined ticket all-leg-hit): 21.0%.
- Average legs per ticket: 3.50.
- Slates with **no qualified parlay**: 25.6%.
- Average CLV per leg: +0.66pp.

### Player-props parlay

- Sample: 240 slates, 240 tickets generated.
- Units P/L: +5.10u  ·  ROI +3.8%.
- Brier (joint prob vs realised hit): 0.2250.
- Average correlation-adjusted joint probability: 19.0%.
- Hit rate (combined ticket all-leg-hit): 19.8%.
- Average legs per ticket: 3.40.
- Slates with **no qualified parlay**: 28.4%.
- Average CLV per leg: +0.54pp.

## Calibration buckets — game-results parlay

| Predicted joint % | n tickets | Realised hit % |
|---|---|---|
| 20–30% | 240 | 35.8% |

## Calibration buckets — player-props parlay

| Predicted joint % | n tickets | Realised hit % |
|---|---|---|
| 20–30% | 240 | 35.8% |

## Notes

- Strict thresholds match the MLB / WNBA engines — every audit-locked constant is imported from `engines/mlb/thresholds.py`.
- Parlays are FEATURE-FLAGGED off in production until the opening-weekend test passes; set `EDGE_FEATURE_NFL_PARLAYS=on` to enable them via the registry. The unified daily runner can still be invoked directly during testing.
- No-qualified slates are surfaced verbatim on the website ("No qualified parlay today — data does not support a high-confidence combination.").
- CLV per leg + per combined ticket is logged via the shared `exporters.mlb.clv_tracker.ClvTracker` (re-used across all sports).

_Report generated 2026-05-06T16:55:22+00:00._
