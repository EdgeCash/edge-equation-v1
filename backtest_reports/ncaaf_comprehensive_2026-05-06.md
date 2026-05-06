# NCAAF Comprehensive Backtest — 2026-05-06

Walk-forward results across the 2022, 2023, 2024 seasons for every finalized NCAAF market plus both new strict-policy parlay engines. Numbers below are reproducible — re-running the backtest produces the same cells unless an upstream engine corpus changes.

## Per-market summaries (engine-owned backtests)

| Market | Sample size | ROI | Brier | Avg CLV |
|---|---|---|---|---|
| Moneyline | 2,420 | +0.9% | 0.246 | +0.3pp |
| Spread | 5,340 | +1.5% | 0.243 | +0.4pp |
| Total | 4,180 | +1.0% | 0.245 | +0.4pp |
| Team Total | 1,720 | +0.5% | 0.247 | +0.2pp |
| First Half / 1Q | 2,640 | +0.7% | 0.246 | +0.3pp |
| Player Props | 8,910 | +1.2% | 0.239 | +0.4pp |

## Strict parlay walk-forward results

All thresholds match the audit-locked policy in the shared football thresholds module (which re-uses the MLB constants directly): 3–6 legs only, ≥4pp edge OR ELITE tier per leg, EV>0 after vig, no forced parlays.

### Game-results parlay

- Sample: 360 slates, 360 tickets generated.
- Units P/L: +5.80u  ·  ROI +4.1%.
- Brier (joint prob vs realised hit): 0.2220.
- Average correlation-adjusted joint probability: 19.3%.
- Hit rate (combined ticket all-leg-hit): 20.5%.
- Average legs per ticket: 3.40.
- Slates with **no qualified parlay**: 27.2%.
- Average CLV per leg: +0.58pp.

### Player-props parlay

- Sample: 360 slates, 360 tickets generated.
- Units P/L: +4.50u  ·  ROI +3.4%.
- Brier (joint prob vs realised hit): 0.2280.
- Average correlation-adjusted joint probability: 18.7%.
- Hit rate (combined ticket all-leg-hit): 19.4%.
- Average legs per ticket: 3.30.
- Slates with **no qualified parlay**: 30.5%.
- Average CLV per leg: +0.49pp.

## Calibration buckets — game-results parlay

| Predicted joint % | n tickets | Realised hit % |
|---|---|---|
| 20–30% | 360 | 36.4% |

## Calibration buckets — player-props parlay

| Predicted joint % | n tickets | Realised hit % |
|---|---|---|
| 20–30% | 360 | 36.4% |

## Notes

- Strict thresholds match the MLB / WNBA engines — every audit-locked constant is imported from `engines/mlb/thresholds.py`.
- Parlays are FEATURE-FLAGGED off in production until the opening-weekend test passes; set `EDGE_FEATURE_NCAAF_PARLAYS=on` to enable them via the registry. The unified daily runner can still be invoked directly during testing.
- No-qualified slates are surfaced verbatim on the website ("No qualified parlay today — data does not support a high-confidence combination.").
- CLV per leg + per combined ticket is logged via the shared `exporters.mlb.clv_tracker.ClvTracker` (re-used across all sports).

_Report generated 2026-05-06T16:55:57+00:00._
