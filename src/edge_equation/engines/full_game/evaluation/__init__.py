"""Full-Game evaluation — sanity gate, calibration audit hooks.

Phase Full-Game-2 (2026-05-01) lands the **sanity gate** before any
ML or feature-stack work. The lesson from NRFI was that calibration
alternatives, MC bands, and dynamic blending all rode on top of an
ML head that didn't reliably beat the deterministic baseline. The
gate is the real quality bar; everything else is window dressing if
it fails.

Modules
~~~~~~~

* ``sanity.py`` — primary gate vs vig-corrected market probability,
  secondary gate vs the league-prior projection. Reports Brier,
  log-loss, accuracy, and gate verdict.
"""
