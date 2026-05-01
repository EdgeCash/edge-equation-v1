"""Props evaluation — sanity gate, calibration audit hooks.

Phase Props-4 (2026-05-01) lands the **sanity gate** before any ML
infrastructure. The lesson from NRFI was that calibration alternatives,
SHAP, MC bands, and dynamic blending all rode on top of an ML head
that didn't reliably beat the deterministic baseline. The gate is the
real quality bar; everything else is window dressing if it fails.

Modules
~~~~~~~

* ``sanity.py`` — primary gate vs vig-corrected market probability,
  secondary gate vs league prior. Reports Brier, log-loss, accuracy,
  and gate verdict.
"""
