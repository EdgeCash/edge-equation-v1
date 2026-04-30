"""NFL calibration layer — skeleton.

Football probabilities need different calibration than baseball:

* **Spread calibration** — discrete margins (especially -3 / +3 /
  -7 / +7 due to scoring structure) cluster around key numbers.
  Naive isotonic regression smooths through them; we want a
  calibration step that respects the key-number grid. Plan: fit
  isotonic on broader bins (3-point grid) then add a small lookup
  adjustment at exact key numbers.
* **Total calibration** — totals also cluster (-44 / -47 / -48
  patterns from common scoring combos). Same key-number treatment.
* **Player-prop calibration** — per-player Bayesian shrinkage on top
  of league-average priors, scaled by snap-share volatility.

Phase F-1 ships none of this — just the package shape.
"""
