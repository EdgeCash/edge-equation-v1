"""Central configuration for the elite NRFI/YRFI engine.

Everything tunable lives here as a frozen dataclass so behaviour stays
deterministic and auditable. Override at runtime by constructing a new
NRFIConfig and passing it to the relevant modules — never mutate the
default in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


# Repository-relative paths. Resolve via NRFIConfig.resolve_paths() so
# tests can point at temporary directories.
#
# We walk up from this file looking for `pyproject.toml` to find the
# repo root. Pre-Phase-1 the engine lived at `nrfi/config.py` so a
# hard-coded `parents[1]` happened to land on the repo root. After the
# migration to `src/edge_equation/engines/nrfi/config.py` (PR #68) the
# old hard-coded depth started silently resolving to
# `src/edge_equation/engines/` and the DuckDB store + model artifacts
# were written to a directory the workflow upload step never looked at.
# Anchoring on `pyproject.toml` is depth-agnostic and won't regress on
# future relocations.
def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    # Defensive fallback — shouldn't ever fire because pyproject.toml
    # is always present in this repo.
    return cur.parents[-1]


_REPO_ROOT = _find_repo_root(Path(__file__))
_DEFAULT_CACHE_DIR = _REPO_ROOT / "data" / "nrfi_cache"
_DEFAULT_DB_PATH = _DEFAULT_CACHE_DIR / "nrfi.duckdb"
_DEFAULT_MODEL_DIR = _REPO_ROOT / "data" / "nrfi_models"


@dataclass(frozen=True)
class CalibrationKnobs:
    """Layer-level tuning constants — match the deterministic v3 engine
    described in nrfi/README.md so the ML stack and Poisson baseline
    operate on a comparable substrate."""

    first_inn_era_factor: float = 0.665
    top_order_weight: float = 0.40
    form_w_season: float = 0.30
    form_w_l10: float = 0.40
    form_w_l5: float = 0.30
    platoon_max_adj: float = 0.12
    ump_weight: float = 0.60
    temp_coeff: float = 0.002       # +0.2% λ per °F over 70F baseline
    wind_out_coeff: float = 0.008   # +0.8% λ per mph blowing out
    wind_in_coeff: float = 0.010    # −1.0% λ per mph blowing in
    humidity_coeff: float = 0.0008  # +0.08% λ per %RH over 50%
    fip_blend: float = 0.55
    k_pct_weight: float = 0.10
    bb_pct_weight: float = 0.15

    def __post_init__(self) -> None:
        if not 0 <= self.fip_blend <= 1:
            raise ValueError("fip_blend must be in [0,1]")
        s = self.form_w_season + self.form_w_l10 + self.form_w_l5
        if abs(s - 1.0) > 1e-6:
            raise ValueError(
                f"form weights must sum to 1.0 (got {s:.4f})"
            )


@dataclass(frozen=True)
class ModelConfig:
    """Hyperparameters and toggles for the ML stack."""

    # Primary classifier — predicts P(NRFI=1)
    xgb_classifier_params: Mapping[str, object] = field(
        default_factory=lambda: {
            "n_estimators": 800,
            "max_depth": 5,
            "learning_rate": 0.03,
            "subsample": 0.85,
            "colsample_bytree": 0.85,
            "min_child_weight": 8,
            "reg_alpha": 0.05,
            "reg_lambda": 1.5,
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "tree_method": "hist",
            "n_jobs": -1,
        }
    )
    # Secondary regressor — predicts λ (expected first-inning runs)
    xgb_poisson_params: Mapping[str, object] = field(
        default_factory=lambda: {
            "n_estimators": 600,
            "max_depth": 4,
            "learning_rate": 0.04,
            "subsample": 0.85,
            "colsample_bytree": 0.85,
            "min_child_weight": 8,
            "reg_lambda": 1.5,
            "objective": "count:poisson",
            "eval_metric": "poisson-nloglik",
            "tree_method": "hist",
            "n_jobs": -1,
        }
    )
    # LightGBM mirror — useful for ensembling / sanity checks
    lgbm_params: Mapping[str, object] = field(
        default_factory=lambda: {
            "n_estimators": 800,
            "max_depth": -1,
            "num_leaves": 31,
            "learning_rate": 0.03,
            "min_child_samples": 20,
            "subsample": 0.85,
            "colsample_bytree": 0.85,
            "reg_alpha": 0.05,
            "reg_lambda": 1.5,
            "objective": "binary",
            "metric": "binary_logloss",
            "verbose": -1,
            "n_jobs": -1,
        }
    )
    # Convex blend between the ML classifier output and the Poisson
    # baseline (P_NRFI = exp(-λ_total)). 0.0 = pure Poisson, 1.0 = pure ML.
    ml_blend_weight: float = 0.65
    # Calibration method: "isotonic" or "platt".
    calibration_method: str = "isotonic"
    # Holdout fraction used to fit the calibrator.
    calibration_holdout_frac: float = 0.20
    # Min samples per calibration bucket for diagnostic plots.
    calibration_min_per_bucket: int = 25


@dataclass(frozen=True)
class MonteCarloConfig:
    """Settings for the per-PA Monte Carlo refinement layer."""

    # 15k strikes the audit-recommended balance between speed and CI
    # tightness. Tuned this with phase-3 polish so live daily runs land
    # in <2s per game.
    n_simulations: int = 15_000
    confidence_alpha: float = 0.10  # 90% CI by default
    rng_seed: int = 7
    # Cap PAs per half-inning to avoid pathological infinite loops if a
    # bad rate sneaks in; 12 is well past league worst-case.
    max_pa_per_half: int = 12


@dataclass(frozen=True)
class BettingConfig:
    """Edge / Kelly thresholds for stake sizing."""

    min_edge_to_bet: float = 0.04        # 4% over implied
    kelly_fraction: float = 0.25          # 1/4 Kelly
    vig_buffer: float = 0.02              # haircut implied prob by 2pts
    max_stake_units: float = 2.0          # safety cap per pick
    default_juice: float = -110.0
    # Probability threshold above which a pick is "green" (deep-green
    # band). Used by the green-only ROI mode in the backtest and by
    # any caller that wants to gate stakes on conviction.
    green_threshold: float = 0.70


@dataclass(frozen=True)
class APIConfig:
    """External endpoint roots & polite-throttle caps."""

    mlb_stats_api_base: str = "https://statsapi.mlb.com/api/v1"
    open_meteo_forecast: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive: str = "https://archive-api.open-meteo.com/v1/archive"
    baseball_savant_abs: str = "https://baseballsavant.mlb.com/abs"
    user_agent: str = "EdgeEquation-NRFI/0.1 (+https://edgeequation.example)"
    requests_per_minute: int = 90  # global token bucket cap
    request_timeout_s: float = 15.0


@dataclass(frozen=True)
class NRFIConfig:
    """Root config object passed through the pipeline."""

    cache_dir: Path = _DEFAULT_CACHE_DIR
    duckdb_path: Path = _DEFAULT_DB_PATH
    model_dir: Path = _DEFAULT_MODEL_DIR

    calibration: CalibrationKnobs = field(default_factory=CalibrationKnobs)
    model: ModelConfig = field(default_factory=ModelConfig)
    monte_carlo: MonteCarloConfig = field(default_factory=MonteCarloConfig)
    betting: BettingConfig = field(default_factory=BettingConfig)
    api: APIConfig = field(default_factory=APIConfig)

    # Toggles
    enable_abs_2026: bool = True   # Use 2026+ ABS Challenge data
    enable_monte_carlo: bool = True
    enable_shap: bool = True
    log_level: str = "INFO"

    def resolve_paths(self) -> "NRFIConfig":
        """Ensure all directory paths exist on disk."""
        for p in (self.cache_dir, self.model_dir):
            Path(p).mkdir(parents=True, exist_ok=True)
        Path(self.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
        return self


def get_default_config() -> NRFIConfig:
    """Return a freshly-resolved default NRFIConfig."""
    return NRFIConfig().resolve_paths()
