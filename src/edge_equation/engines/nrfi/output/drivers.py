"""Human-readable model driver formatting for NRFI output.

Tree SHAP values are useful for audit, but raw feature names such as
``int_top3_obp_vs_home_p_x_bb`` are not fit for a public daily report.  This
module turns the top SHAP pairs into compact notes with capped impact scores.
The sign is preserved from SHAP: positive values push the model toward NRFI,
negative values push it away from NRFI.
"""

from __future__ import annotations

from typing import Sequence


_FEATURE_LABELS: dict[str, str] = {
    "lambda_total": "projected first-inning run pressure",
    "lambda_home_half": "projected bottom-1st run pressure",
    "lambda_away_half": "projected top-1st run pressure",
    "poisson_p_nrfi": "baseline NRFI probability",
    "park_factor_runs": "park run environment",
    "park_factor_hr": "park home-run profile",
    "wx_temperature_f": "game-time temperature",
    "wx_humidity_pct": "humid air conditions",
    "wx_dew_point_f": "dew point",
    "wx_air_density": "air density",
    "wx_wind_out_mph": "wind blowing out",
    "wx_wind_in_mph": "wind blowing in",
    "wx_wind_speed_mph": "wind speed",
    "wx_wind_signed_axis": "wind direction",
    "wx_precip_prob": "precipitation risk",
    "wx_indoor": "indoor/roof conditions",
    "ump_zone_idx": "home-plate umpire zone",
    "ump_run_env_idx": "umpire run environment",
    "int_temp_x_park_runs": "temperature and park run fit",
    "int_wind_x_park_hr": "wind and park home-run fit",
    "int_top3_obp_vs_home_p_x_bb": "opposing top-3 OBP versus home starter walk risk",
    "int_top3_obp_vs_away_p_x_bb": "opposing top-3 OBP versus away starter walk risk",
}


def format_driver_notes(
    shap_drivers: Sequence[tuple[str, float]],
    *,
    max_drivers: int = 4,
) -> list[str]:
    """Return public-facing driver notes.

    SHAP values are model-space contributions.  For display we convert them to
    a simple capped score from 1 to 14 so a tiny, undertrained sample cannot
    print absurd "+185" impacts.  The score is directional, not a literal
    probability-point claim.
    """

    notes: list[str] = []
    for feature, value in list(shap_drivers)[:max_drivers]:
        impact = _impact_score(value)
        sign = "+" if float(value) >= 0 else "-"
        notes.append(f"{sign}{impact} from {_feature_label(feature)}")
    return notes


def _impact_score(value: float) -> int:
    """Capped display impact score for a SHAP value."""

    return max(1, min(14, int(round(abs(float(value)) * 30.0))))


def _feature_label(feature: str) -> str:
    if feature in _FEATURE_LABELS:
        return _FEATURE_LABELS[feature]

    # Pitcher features.
    for prefix, starter in (
        ("home_p_", "home starter"),
        ("away_p_", "away starter"),
    ):
        if feature.startswith(prefix):
            suffix = feature[len(prefix):]
            return f"{starter} {_clean_suffix(suffix)}"

    # Offense/lineup features from the perspective of the pitcher faced.
    for prefix, label in (
        ("vs_home_p_", "opposing lineup vs home starter"),
        ("vs_away_p_", "opposing lineup vs away starter"),
    ):
        if feature.startswith(prefix):
            suffix = feature[len(prefix):]
            return f"{label} {_clean_suffix(suffix)}"

    return _clean_suffix(feature)


def _clean_suffix(suffix: str) -> str:
    phrase_map = {
        "xera": "xERA",
        "era": "ERA",
        "fip": "FIP",
        "xfip": "xFIP",
        "k_pct": "strikeout rate",
        "bb_pct": "walk rate",
        "hr_pct": "home-run rate",
        "first_inn_k_pct": "first-inning strikeout rate",
        "first_inn_bb_pct": "first-inning walk rate",
        "first_inn_hr_pct": "first-inning home-run rate",
        "first_inn_runs_per": "first-inning runs allowed rate",
        "first_inn_pa": "first-inning PA sample",
        "top3_obp": "top-3 OBP",
        "top3_obp_raw": "top-3 raw OBP",
        "top3_wrc": "top-3 wRC+",
        "top3_iso": "top-3 ISO",
        "top3_k_pct": "top-3 strikeout rate",
        "top3_bb_pct": "top-3 walk rate",
        "top3_woba_vs_hand": "top-3 wOBA versus hand",
        "top4_obp": "top-4 OBP",
        "lineup_confirmed": "confirmed lineup",
        "bf_sample": "batters-faced sample",
    }
    if suffix in phrase_map:
        return phrase_map[suffix]
    return suffix.replace("_", " ").strip()

