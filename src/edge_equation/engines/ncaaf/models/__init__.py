"""NCAAF projection models — skeleton.

Same structure as the NFL `models/` package. Two open design
questions specific to college football:

* **Composite ratings as a prior** — should we blend toward a
  recruit-ratings-driven team strength rather than raw league
  average? Likely yes, especially weeks 1-3 when sample is tiny.
* **Bowl game treatment** — opt-outs, transfer-portal entry, and
  motivation effects make bowl games their own modeling regime.
  We may want a separate calibration layer for postseason games.

Phase F-1 ships none of this — just the package shape.
"""
