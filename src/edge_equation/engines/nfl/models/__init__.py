"""NFL projection models — skeleton.

Mirrors the MLB engine's `models/inference.py` + `model_training.py`
split:

* ``projection.py`` (planned) — per-game expected points (home/away)
  via Poisson-shifted Skellam, blended Bayesian shrinkage on
  per-team rates, plus QB / rest / weather adjustments stacked on
  top.
* ``inference.py`` (planned) — bundle loader + predict-for-features
  callable. R2-backed bundle storage like NRFI's once we have a
  trained model to ship.
* ``model_training.py`` (planned) — XGBoost (per-team strength) +
  isotonic calibration (per-market spread/total). Walk-forward
  trainer that respects the bye-week structure of the NFL season.

Phase F-1 ships none of this — just the package shape.
"""
