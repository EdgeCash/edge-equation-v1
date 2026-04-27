"""Feature engineering layers for the NRFI/YRFI engine.

Two modules:

* `splits.py` — exponentially-weighted rolling stats, percentile helpers,
  first-inning aggregations from Statcast pitch frames.
* `feature_engineering.py` — orchestrates all layers (pitcher, batter,
  umpire, weather, park, team) plus interaction terms and the 2026 ABS
  Challenge System adjustments. Output: a flat `dict[str, float]` per
  game ready for the ML stack.
"""
