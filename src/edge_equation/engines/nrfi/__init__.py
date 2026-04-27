"""Edge Equation — Elite NRFI/YRFI prediction engine.

Modular subpackage that augments the deterministic 7-layer NRFI engine
described in `nrfi/README.md` with:

* Pluggable ML models (XGBoost / LightGBM) layered on top of a calibrated
  Poisson baseline.
* Per-PA Monte Carlo simulation for refined probability + confidence band.
* Isotonic / Platt calibration on a holdout window.
* SHAP-driven explanations for every published pick.
* Full historical backtest with point-in-time feature reconstruction.
* 2026 ABS Challenge System integration.

Heavy ML / scraping dependencies (xgboost, lightgbm, shap, pybaseball,
duckdb, ...) are imported lazily inside the modules that need them so the
core deterministic Edge Equation package keeps its slim install footprint.
Install the optional stack via:

    pip install -r nrfi/requirements-nrfi.txt
"""

from .config import NRFIConfig, get_default_config

__all__ = ["NRFIConfig", "get_default_config"]
__version__ = "0.1.0"
