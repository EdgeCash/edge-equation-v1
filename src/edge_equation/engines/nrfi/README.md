# THE EDGE EQUATION — NRFI/YRFI Engine v3.0

**Analytics. Not Feelings.**

7-layer probability engine for MLB first-inning scoring predictions.
Outputs P(NRFI) and P(YRFI) for every game on the daily slate.

---

## Architecture

| Layer | Name | What It Does | Key Inputs |
|-------|------|-------------|------------|
| L1 | Poisson Base | ERA/WHIP → λ (expected runs per half-inning) | ERA, WHIP, IP, park factor |
| L2 | Top-of-Order Offense | 1-2-3 hitters' OBP (not team-wide) | Lineup OBP via boxscore API |
| L3 | Recent Form | Blends Season / L10 / L5 pitcher stats | Game log rolling windows |
| L4 | Platoon Splits | Pitcher hand vs lineup RHH/LHH composition | Pitcher hand, team platoon splits |
| L5 | Umpire Zone | HP umpire historical run-environment factor | 70+ umpire database |
| L6 | Weather | Temperature, wind speed+direction, humidity | Open-Meteo API, ballpark coords |
| L7 | Advanced Pitcher | FIP/ERA blend + K%/BB% modifiers | FIP, K%, BB% from counting stats |

### Core Formula

```
P(NRFI) = P(0 runs top 1st) × P(0 runs bottom 1st)

Each half-inning λ =
    advanced_pitcher_lambda(pitcher)     L1 + L7
  × park_factor                          L1
  × top_order_factor(opposing 1-3 OBP)   L2
  × platoon_factor(hand vs lineup)       L4
  × umpire_factor(HP ump)                L5
  × weather_factor(conditions)           L6

P(0 runs in half-inning) = e^(-λ)       Poisson
```

Layer 3 (Recent Form) is applied upstream — it blends the pitcher's ERA/WHIP
inputs before they enter the λ calculation.

---

## Data Sources

| Source | Cost | What It Provides |
|--------|------|-----------------|
| MLB Stats API | Free, no key | Schedule, pitchers, lineups, game logs, umpires, linescores |
| Open-Meteo API | Free, no key | Hourly weather by coordinates (forecast + archive) |

---

## Files

```
nrfi/
├── nrfi_engine.py      # 7-layer core model (37KB)
├── nrfi_backtest.py     # Historical accuracy validator (20KB)
└── README.md            # This file
```

---

## Usage

### Daily Engine Run
```bash
python3 nrfi_engine.py                # today's slate
python3 nrfi_engine.py 2026-04-27     # specific date
```

Output: Ranked NRFI/YRFI board + `nrfi_output.json` for graphic pipeline.

### Backtesting
```bash
python3 nrfi_backtest.py                          # last 7 days
python3 nrfi_backtest.py 2026-04-01 2026-04-20    # custom range
python3 nrfi_backtest.py 2026-04-01 2026-04-20 --fast  # skip weather/lineups
python3 nrfi_backtest.py --validate               # offline scoring logic test
```

Output: Full report (accuracy, Brier score, calibration, simulated ROI) + JSON.

---

## Calibration (8/8 Checks Passed)

| Scenario | NRFI% | Expected |
|----------|-------|----------|
| League avg everything @ neutral park | 55.1% | 52-57% |
| Ace vs Ace @ Dodger Stadium | 69.5% | 65-82% |
| Bad vs Bad @ Coors + hot + wind out + tight ump | 28.3% | 20-40% |
| Weather isolation (cold+in vs hot+out) | 9.9pp swing | >2pp |
| Umpire isolation (wide vs tight zone) | 2.2pp swing | >1.5pp |
| 1-3 OBP isolation (.400 vs .260) | 5.7pp swing | >3pp |
| FIP divergence (lucky vs legit 3.00 ERA) | 9.7pp swing | >2pp |
| Dome park weather neutralization | 1.0 (exact) | 1.0 |

---

## Tunable Constants

All constants are at the top of `nrfi_engine.py` in Section 1.
Key knobs:

| Constant | Default | What It Controls |
|----------|---------|-----------------|
| `FIRST_INN_ERA_FACTOR` | 0.665 | Master calibration knob for baseline λ |
| `TOP_ORDER_WEIGHT` | 0.40 | How much 1-3 OBP shifts λ |
| `FORM_W_SEASON/L10/L5` | 0.30/0.40/0.30 | Recency bias in pitcher blending |
| `PLATOON_MAX_ADJ` | 0.12 | Max ±12% λ shift from handedness |
| `UMP_WEIGHT` | 0.60 | Dampening on umpire factor |
| `TEMP_COEFF` | 0.002 | 0.2% per °F |
| `WIND_OUT_COEFF` | 0.008 | 0.8% per mph tailwind |
| `WIND_IN_COEFF` | 0.010 | 1.0% per mph headwind |
| `FIP_BLEND` | 0.55 | Weight on FIP vs ERA |
| `K_PCT_WEIGHT` | 0.10 | Max λ shift from K% |
| `BB_PCT_WEIGHT` | 0.15 | Max λ shift from BB% |

---

## Output Signals

| Signal | Meaning |
|--------|---------|
| **LOCK** | 15%+ edge from 50% |
| **STRONG** | 10-15% edge |
| **MODERATE** | 5-10% edge |
| **LOW** | Under 5% edge |

---

## Backtest Metrics

| Metric | What It Measures |
|--------|-----------------|
| **Accuracy** | % of games where model's lean (NRFI/YRFI) matched actual |
| **Brier Score** | Calibration quality (0 = perfect, 0.25 = coin flip) |
| **Calibration** | Predicted % vs actual % in 10% buckets |
| **Simulated ROI** | Flat-bet P&L at -110 juice, min 4% edge threshold |
