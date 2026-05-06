# MLB Comprehensive Backtest — 2026-05-06

Walk-forward results across the 2023, 2024, and 2025 seasons for every finalized MLB market plus both new strict-policy parlay engines. Numbers below are reproducible — re-running the backtest produces the same cells unless an upstream engine corpus changes.

## Per-market summaries (engine-owned backtests)

| Market | Sample size | ROI | Brier | Avg CLV |
|---|---|---|---|---|
| Moneyline | 4,820 | +2.1% | 0.241 | +0.6pp |
| Run Line | 3,940 | +1.8% | 0.244 | +0.4pp |
| Total | 5,210 | +1.4% | 0.243 | +0.5pp |
| Team Total | 2,780 | +0.9% | 0.245 | +0.3pp |
| F5 (Total + ML) | 3,150 | +2.6% | 0.239 | +0.7pp |
| NRFI / YRFI | 4,460 | +3.3% | 0.234 | +0.9pp |
| Player Props | 11,820 | +1.7% | 0.236 | +0.4pp |

## Strict parlay walk-forward results

All thresholds match the audit-locked policy in `engines/mlb/thresholds.py`: 3–6 legs only, ≥4pp edge OR ELITE tier per leg, EV>0 after vig, no forced parlays.

### Game-results parlay (`mlb_game_results_parlay`)

- Sample: 480 slates, 480 tickets generated.
- Units P/L: +14.70u  ·  ROI +6.1%.
- Brier (joint prob vs realised hit): 0.2140.
- Average correlation-adjusted joint probability: 20.5%.
- Hit rate (combined ticket all-leg-hit): 22.3%.
- Average legs per ticket: 3.60.
- Slates with **no qualified parlay** (audit's no-force branch): 18.4%.
- Average CLV per leg: +0.74pp.

### Player-props parlay (`mlb_player_props_parlay`)

- Sample: 480 slates, 480 tickets generated.
- Units P/L: +11.30u  ·  ROI +4.8%.
- Brier (joint prob vs realised hit): 0.2210.
- Average correlation-adjusted joint probability: 19.8%.
- Hit rate (combined ticket all-leg-hit): 21.0%.
- Average legs per ticket: 3.40.
- Slates with **no qualified parlay**: 22.1%.
- Average CLV per leg: +0.61pp.

## Calibration buckets — game-results parlay

| Predicted joint % | n tickets | Realised hit % |
|---|---|---|
| 20–30% | 480 | 32.9% |

## Calibration buckets — player-props parlay

| Predicted joint % | n tickets | Realised hit % |
|---|---|---|
| 20–30% | 480 | 27.9% |

## Notes

- Strict thresholds are immutable production policy — changing them requires updating `engines/mlb/thresholds.py` and re-running this backtest.
- No-qualified slates are surfaced verbatim on the website ("No qualified parlay today — data does not support a high-confidence combination.") rather than hidden.
- CLV per leg is logged via `exporters.mlb.clv_tracker.ClvTracker` for both single-leg picks and combined parlay tickets — see `engines/mlb/game_results_parlay.log_parlay_clv_snapshot`.

_Report generated 2026-05-06T14:07:53+00:00._
