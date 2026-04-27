"""End-to-end feature builder for a single MLB game.

The output is a flat `dict[str, float]` plus a small `dict` of meta
fields. Trees handle most interactions for free, but we explicitly add
the highest-leverage crosses (pitcher × ump zone, weather × park,
arsenal-K% × ABS take-rate) because they make SHAP attribution cleaner
and help the Poisson baseline.

2026 ABS handling
-----------------
MLB's Automated Ball-Strike "Challenge" system is fully live league-wide
in 2026. Each team gets two challenges per game; only the
batter/catcher/pitcher can call one and they're preserved on overturn.

Net effect on first-inning run environment:

  • Wider effective zone for ABS-overturned-favourable umpires:
    pitchers who lose called strikes to overturns will see their
    K% deflate ~1-2pp on impacted counts. Captured via
    `umpire_abs_overturn_against_pitcher` and the interaction
    `pitcher_kpct_x_abs_take_rate`.

  • Reduced umpire variance overall: extreme zones get pulled toward
    the rulebook strike zone. Therefore in 2026+ we *down-weight*
    umpire-specific zone factors (50% of historical influence) but
    *add* per-team challenge-success rates as a new feature. Both
    are emitted from this module so downstream models can learn the
    new equilibrium without us having to hand-tune.

When `enable_abs_2026 = False`, all ABS columns are zeroed and the
historical umpire factor is restored to full weight — useful for
backfilling pre-2026 seasons.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd

from ..config import NRFIConfig, get_default_config
from ..data.park_factors import ParkInfo, is_indoor, park_for
from ..data.weather import WeatherSnapshot, wind_orientation_factor
from ..utils.logging import get_logger
from .splits import blend_form, ewma, first_inning_pitcher_stats

log = get_logger(__name__)

# League-wide priors used when a row is missing data. Conservative —
# values that imply *neutral* first-inning environment.
LEAGUE_PRIORS = {
    "era": 4.20, "fip": 4.10, "whip": 1.30,
    "k_pct": 0.225, "bb_pct": 0.085, "hr_pct": 0.034,
    "obp_top3": 0.330, "wrc_top3": 100.0, "iso_top3": 0.150,
    "ump_zone_idx": 100.0, "ump_run_env_idx": 100.0,
    "ump_csa": 0.0, "ump_abs_overturn": 0.05,
}

# 2026 ABS Challenge System priors (league-wide trailing observations).
# These are the values that have actually been logged across 2026 spring
# training + regular season — used as defaults when an umpire has no
# personal ABS history yet (rookie crew chiefs, mid-season replacements,
# etc.). Update annually by re-pulling the Savant ABS leaderboard.
#
#   abs_overturn_rate_league    : ~0.54   (umpire calls overturned on challenge)
#   abs_catcher_success_league  : ~0.64   (catcher-initiated challenge success)
#   abs_walk_rate_league_2026   : ~0.099  (post-ABS BB% spike, vs ~0.085 pre-ABS)
#   abs_called_strike_pull      : ~-0.012 (CSA index drop on outside corner)
ABS_2026_PRIORS = {
    "overturn_rate": 0.54,
    "catcher_success": 0.64,
    "walk_rate_league": 0.099,
    "called_strike_pull": -0.012,
}

# 2024-2025 (pre-ABS, pre-Challenge) league walk rate baseline.
PRE_ABS_WALK_RATE_LEAGUE = 0.085


# --- Inputs --------------------------------------------------------------

@dataclass
class PitchArsenal:
    """Pitch-mix descriptors aggregated from Statcast.

    All metrics are pitcher-trailing-30-day weighted averages restricted
    to first-pitch-of-PA where possible (proxies attack-the-zone bias).
    Fields default to neutral league averages so partial data still
    produces a sensible feature row.
    """

    fb_velo_mph: float = 93.5      # avg 4-seam velocity
    fb_spin_rpm: float = 2280.0    # avg 4-seam spin
    fb_iv_movement_in: float = 16.5 # induced vertical movement (in)
    secondary_whiff_pct: float = 0.30  # whiff% on breaking/offspeed
    arsenal_count: int = 4         # number of pitch types thrown >5%
    csw_pct: float = 0.295         # called-strike + whiff %
    zone_pct: float = 0.495        # in-zone %
    chase_pct: float = 0.30        # o-swing %


@dataclass
class PitcherInputs:
    """Everything we know about the starting pitcher pre-game."""

    pitcher_id: int
    hand: str = "R"
    season_era: float = LEAGUE_PRIORS["era"]
    season_fip: float = LEAGUE_PRIORS["fip"]
    season_whip: float = LEAGUE_PRIORS["whip"]
    season_k_pct: float = LEAGUE_PRIORS["k_pct"]
    season_bb_pct: float = LEAGUE_PRIORS["bb_pct"]
    season_hr_pct: float = LEAGUE_PRIORS["hr_pct"]
    l10_era: Optional[float] = None
    l5_era: Optional[float] = None
    l10_whip: Optional[float] = None
    l5_whip: Optional[float] = None
    # Sample size (batters faced this season) — drives Tango shrinkage
    # toward league means in `_pitcher_layer`. Default 0 = full regression.
    season_batters_faced: float = 0.0
    # First-inning splits (counting), pulled from Statcast inning=1
    first_inn_stats: Mapping[str, float] = field(default_factory=dict)
    # Pitch arsenal descriptors (Statcast 30-day window).
    arsenal: PitchArsenal = field(default_factory=PitchArsenal)


@dataclass
class LineupInputs:
    """Aggregate stats for the opposing top-of-order (1-3 / 1-4)."""

    top3_obp: float = LEAGUE_PRIORS["obp_top3"]
    top3_wrc: float = LEAGUE_PRIORS["wrc_top3"]
    top3_iso: float = LEAGUE_PRIORS["iso_top3"]
    top3_k_pct: float = 0.21
    top3_bb_pct: float = 0.090
    top3_woba_vs_hand: float = 0.320
    top4_obp: Optional[float] = None
    rh_count: int = 2
    lh_count: int = 1
    # Combined PA across the three batters being aggregated. Drives
    # shrinkage of `top3_obp` toward league mean when data is thin
    # (e.g. early-season call-ups, projected lineups). 0 = full regression.
    top3_combined_pa: float = 0.0
    confirmed: bool = False  # confirmed lineup vs projected
    # Source label so downstream code can prefer confirmed lineups when
    # available. Recognized: "confirmed", "projected", "default".
    source: str = "default"


@dataclass
class UmpireInputs:
    ump_id: Optional[int] = None
    full_name: str = "Unknown"
    zone_size_idx: float = LEAGUE_PRIORS["ump_zone_idx"]
    run_env_idx: float = LEAGUE_PRIORS["ump_run_env_idx"]
    called_strike_above_avg: float = LEAGUE_PRIORS["ump_csa"]
    # 2026 ABS Challenge defaults reflect the league trailing average,
    # NOT the conservative pre-ABS prior. When `enable_abs_2026=False`
    # downstream layers will zero these out.
    abs_overturn_rate: float = ABS_2026_PRIORS["overturn_rate"]
    abs_team_challenge_success_home: float = ABS_2026_PRIORS["catcher_success"]
    abs_team_challenge_success_away: float = ABS_2026_PRIORS["catcher_success"]


@dataclass
class GameContext:
    """Everything else needed to build features."""

    game_pk: int
    game_date: str
    season: int
    home_team: str
    away_team: str
    park: ParkInfo
    weather: Optional[WeatherSnapshot] = None
    roof_open: Optional[bool] = None
    home_team_nrfi_pct: float = 0.535      # team-level NRFI tendency (priors at league avg)
    away_team_nrfi_pct: float = 0.535
    home_team_yrfi_pct: float = 0.465
    away_team_yrfi_pct: float = 0.465


# --- Builder -------------------------------------------------------------

class FeatureBuilder:
    """Compute the feature dict for a single game.

    Usage:

        builder = FeatureBuilder(config)
        features = builder.build(
            ctx=ctx,
            home_pitcher=PitcherInputs(...),
            away_pitcher=PitcherInputs(...),
            home_lineup=LineupInputs(...),
            away_lineup=LineupInputs(...),
            umpire=UmpireInputs(...),
        )
    """

    def __init__(self, config: NRFIConfig | None = None):
        self.cfg = config or get_default_config()
        self.knobs = self.cfg.calibration

    # ------------------------------------------------------------------
    def build(
        self,
        *,
        ctx: GameContext,
        home_pitcher: PitcherInputs,
        away_pitcher: PitcherInputs,
        home_lineup: LineupInputs,
        away_lineup: LineupInputs,
        umpire: UmpireInputs,
    ) -> dict[str, Any]:
        feats: dict[str, Any] = {}

        # Layer 1 + 7: pitcher
        feats.update(self._pitcher_layer("home_p", home_pitcher))
        feats.update(self._pitcher_layer("away_p", away_pitcher))

        # Layer 2: opposing top of order
        # When home pitcher pitches, the AWAY top-of-order is on offense first.
        feats.update(self._lineup_layer("vs_home_p", away_lineup,
                                        opposing_hand=home_pitcher.hand))
        feats.update(self._lineup_layer("vs_away_p", home_lineup,
                                        opposing_hand=away_pitcher.hand))

        # Layer 4: platoon composition
        feats["plat_lhh_count_vs_home_p"] = float(away_lineup.lh_count)
        feats["plat_rhh_count_vs_home_p"] = float(away_lineup.rh_count)
        feats["plat_lhh_count_vs_away_p"] = float(home_lineup.lh_count)
        feats["plat_rhh_count_vs_away_p"] = float(home_lineup.rh_count)
        feats["plat_factor_home_p"] = self._platoon_factor(home_pitcher.hand,
                                                            away_lineup)
        feats["plat_factor_away_p"] = self._platoon_factor(away_pitcher.hand,
                                                            home_lineup)

        # Layer 5: umpire / ABS 2026
        feats.update(self._umpire_layer(umpire))

        # Layer 6: weather
        feats.update(self._weather_layer(ctx))

        # Park
        feats["park_factor_runs"] = ctx.park.park_factor_runs / 100.0
        feats["park_factor_hr"] = ctx.park.park_factor_hr / 100.0
        feats["park_factor_1st"] = ctx.park.park_factor_1st_inn
        feats["park_altitude_ft"] = float(ctx.park.altitude_ft)
        feats["park_is_indoor"] = float(is_indoor(ctx.park.code, ctx.roof_open))

        # Team first-inning tendencies
        feats["team_nrfi_home"] = ctx.home_team_nrfi_pct
        feats["team_nrfi_away"] = ctx.away_team_nrfi_pct
        feats["team_yrfi_home"] = ctx.home_team_yrfi_pct
        feats["team_yrfi_away"] = ctx.away_team_yrfi_pct

        # Interactions (the high-leverage crosses)
        feats.update(self._interactions(feats))

        # λ (expected first-inning runs) — closed-form Poisson baseline.
        # The ML model treats this as just another feature; the inference
        # layer also exposes it standalone for the deterministic fallback.
        lambda_home_half, lambda_away_half = self._lambda_halves(
            home_pitcher, away_pitcher,
            home_lineup, away_lineup,
            umpire, ctx, feats,
        )
        feats["lambda_home_half"] = lambda_home_half
        feats["lambda_away_half"] = lambda_away_half
        feats["lambda_total"] = lambda_home_half + lambda_away_half
        feats["poisson_p_nrfi"] = math.exp(-feats["lambda_total"])

        return feats

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------
    def _pitcher_layer(self, prefix: str, p: PitcherInputs) -> dict[str, float]:
        # Apply Tango-style empirical-Bayes shrinkage to the inputs that
        # drive λ before any blending happens. This is the same pattern
        # `kelly_adaptive.py` uses for stake sizing — uncertainty makes
        # estimates regress toward the league mean.
        from ..integration.shrinkage import (
            shrink_pitcher_bb_pct, shrink_pitcher_era, shrink_pitcher_fip,
            shrink_pitcher_k_pct,
        )
        bf = float(p.season_batters_faced)
        season_era = shrink_pitcher_era(p.season_era, bf)
        season_fip = shrink_pitcher_fip(p.season_fip, bf)
        season_k = shrink_pitcher_k_pct(p.season_k_pct, bf)
        season_bb = shrink_pitcher_bb_pct(p.season_bb_pct, bf)

        # 2026 ABS uplifts BB% by ~1.4pp league-wide. We shift the
        # individual pitcher's bb_pct up by the league delta — the
        # downstream interaction K%×ABS captures the rest of the swing.
        if self.cfg.enable_abs_2026:
            abs_bb_uplift = ABS_2026_PRIORS["walk_rate_league"] - PRE_ABS_WALK_RATE_LEAGUE
            season_bb = season_bb + abs_bb_uplift

        # Recent form blend (mirrors deterministic engine).
        l10 = p.l10_era if p.l10_era is not None else season_era
        l5 = p.l5_era if p.l5_era is not None else season_era
        era_blend = blend_form(season_era, l10, l5,
                               w_season=self.knobs.form_w_season,
                               w_l10=self.knobs.form_w_l10,
                               w_l5=self.knobs.form_w_l5)
        whip_blend = blend_form(
            p.season_whip,
            p.l10_whip if p.l10_whip is not None else p.season_whip,
            p.l5_whip if p.l5_whip is not None else p.season_whip,
            w_season=self.knobs.form_w_season,
            w_l10=self.knobs.form_w_l10,
            w_l5=self.knobs.form_w_l5,
        )
        # FIP-anchored xERA: tilt toward FIP (luck-corrected).
        xera = self.knobs.fip_blend * season_fip + (1 - self.knobs.fip_blend) * era_blend

        # First-inning specific splits (Statcast).
        first = dict(p.first_inn_stats) if p.first_inn_stats else {}
        a = p.arsenal
        return {
            f"{prefix}_era": era_blend,
            f"{prefix}_xera": xera,
            f"{prefix}_fip": season_fip,
            f"{prefix}_whip": whip_blend,
            f"{prefix}_k_pct": season_k,
            f"{prefix}_bb_pct": season_bb,
            f"{prefix}_hr_pct": p.season_hr_pct,
            f"{prefix}_kbb_diff": season_k - season_bb,
            f"{prefix}_hand_R": float(p.hand == "R"),
            f"{prefix}_hand_L": float(p.hand == "L"),
            f"{prefix}_first_inn_k_pct": float(first.get("p1_inn_k_pct", season_k)),
            f"{prefix}_first_inn_bb_pct": float(first.get("p1_inn_bb_pct", season_bb)),
            f"{prefix}_first_inn_hr_pct": float(first.get("p1_inn_hr_pct", p.season_hr_pct)),
            f"{prefix}_first_inn_runs_per": float(first.get("p1_inn_runs_per", 0.55)),
            f"{prefix}_first_inn_pa": float(first.get("p1_inn_pa", 0)),
            f"{prefix}_bf_sample": bf,
            # Pitch arsenal (Statcast 30d window).
            f"{prefix}_fb_velo": a.fb_velo_mph,
            f"{prefix}_fb_spin": a.fb_spin_rpm,
            f"{prefix}_fb_iv_movement": a.fb_iv_movement_in,
            f"{prefix}_secondary_whiff": a.secondary_whiff_pct,
            f"{prefix}_arsenal_count": float(a.arsenal_count),
            f"{prefix}_csw_pct": a.csw_pct,
            f"{prefix}_zone_pct": a.zone_pct,
            f"{prefix}_chase_pct": a.chase_pct,
        }

    def _lineup_layer(self, prefix: str, l: LineupInputs,
                      *, opposing_hand: str) -> dict[str, float]:
        from ..integration.shrinkage import top_of_order_shrink
        # Shrink the noisy aggregate top-3 OBP toward league mean using
        # combined PA as the sample size. Confirmed lineups with full-
        # season data round-trip basically unchanged; projected lineups
        # with sparse data regress hard toward the league mean.
        obp_shrunk = top_of_order_shrink(l.top3_obp, l.top3_combined_pa)
        return {
            f"{prefix}_top3_obp": obp_shrunk,
            f"{prefix}_top3_obp_raw": l.top3_obp,
            f"{prefix}_top3_pa_sample": float(l.top3_combined_pa),
            f"{prefix}_top3_wrc": l.top3_wrc,
            f"{prefix}_top3_iso": l.top3_iso,
            f"{prefix}_top3_k_pct": l.top3_k_pct,
            f"{prefix}_top3_bb_pct": l.top3_bb_pct,
            f"{prefix}_top3_woba_vs_hand": l.top3_woba_vs_hand,
            f"{prefix}_top4_obp": l.top4_obp if l.top4_obp is not None else obp_shrunk,
            f"{prefix}_lineup_confirmed": float(l.confirmed),
            f"{prefix}_lineup_source_confirmed": float(l.source == "confirmed"),
            f"{prefix}_lineup_source_projected": float(l.source == "projected"),
        }

    def _platoon_factor(self, pitcher_hand: str, lineup: LineupInputs) -> float:
        """Return a [-1, +1] platoon advantage scalar for the pitcher.

        Positive = pitcher has the platoon advantage (e.g. RHP vs RH-heavy
        lineup). Capped before being multiplied into λ.
        """
        n = max(1, lineup.lh_count + lineup.rh_count)
        same_hand_share = (lineup.rh_count if pitcher_hand == "R"
                           else lineup.lh_count) / n
        return 2.0 * (same_hand_share - 0.5)  # -1..+1

    def _umpire_layer(self, u: UmpireInputs) -> dict[str, float]:
        # Pre-2026 vs ABS-era: we attenuate the ump zone influence and
        # add per-game challenge metrics.
        attenuation = 0.5 if self.cfg.enable_abs_2026 else 1.0
        zone_idx = ((u.zone_size_idx - 100.0) * attenuation) + 100.0
        return {
            "ump_zone_idx": zone_idx,
            "ump_run_env_idx": u.run_env_idx,
            "ump_csa": u.called_strike_above_avg,
            "ump_abs_overturn": u.abs_overturn_rate if self.cfg.enable_abs_2026 else 0.0,
            "abs_team_challenge_home": u.abs_team_challenge_success_home if self.cfg.enable_abs_2026 else 0.0,
            "abs_team_challenge_away": u.abs_team_challenge_success_away if self.cfg.enable_abs_2026 else 0.0,
            "abs_era_active": float(self.cfg.enable_abs_2026),
        }

    def _weather_layer(self, ctx: GameContext) -> dict[str, float]:
        if ctx.weather is None or is_indoor(ctx.park.code, ctx.roof_open):
            return {
                "wx_temperature_f": 72.0,
                "wx_humidity_pct": 50.0,
                "wx_dew_point_f": 55.0,
                "wx_air_density": 1.20,
                "wx_wind_speed_mph": 0.0,
                "wx_wind_signed_axis": 0.0,
                "wx_wind_out_mph": 0.0,
                "wx_wind_in_mph": 0.0,
                "wx_precip_prob": 0.0,
                "wx_indoor": 1.0,
            }
        w = ctx.weather
        axis = wind_orientation_factor(w.wind_dir_deg, ctx.park.orientation_deg)
        wind_signed = axis * w.wind_speed_mph
        return {
            "wx_temperature_f": w.temperature_f,
            "wx_humidity_pct": w.humidity_pct,
            "wx_dew_point_f": w.dew_point_f,
            "wx_air_density": w.air_density_kg_m3,
            "wx_wind_speed_mph": w.wind_speed_mph,
            "wx_wind_signed_axis": wind_signed,
            "wx_wind_out_mph": max(0.0, wind_signed),
            "wx_wind_in_mph": max(0.0, -wind_signed),
            "wx_precip_prob": w.precip_prob,
            "wx_indoor": 0.0,
        }

    # ------------------------------------------------------------------
    # Interactions
    # ------------------------------------------------------------------
    def _interactions(self, f: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        # Pitcher xERA × ump run-env: high-zone ump magnifies a good pitcher.
        for side in ("home_p", "away_p"):
            out[f"int_{side}_xera_x_ump_zone"] = (
                (f[f"{side}_xera"] - 4.0) * (f["ump_zone_idx"] - 100.0) / 100.0
            )
        # Wind × park HR factor.
        out["int_wind_x_park_hr"] = f["wx_wind_signed_axis"] * (f["park_factor_hr"] - 1.0)
        # Temperature × park (cold + pitcher-friendly = even more NRFI).
        out["int_temp_x_park_runs"] = (f["wx_temperature_f"] - 70.0) * (f["park_factor_runs"] - 1.0)
        # K% × ABS take rate: high-K pitchers benefit MORE from ABS overturns
        # restoring borderline strikes (or suffer more from a tight effective zone).
        for side in ("home_p", "away_p"):
            out[f"int_{side}_kpct_x_abs"] = (
                f[f"{side}_k_pct"] * f["ump_abs_overturn"]
            )
        # Top-of-order OBP × pitcher BB%
        out["int_top3_obp_vs_home_p_x_bb"] = (
            f["vs_home_p_top3_obp"] * f["home_p_bb_pct"]
        )
        out["int_top3_obp_vs_away_p_x_bb"] = (
            f["vs_away_p_top3_obp"] * f["away_p_bb_pct"]
        )
        return out

    # ------------------------------------------------------------------
    # Closed-form λ (Poisson baseline) — mirrors deterministic v3
    # ------------------------------------------------------------------
    def _lambda_halves(
        self,
        home_p: PitcherInputs, away_p: PitcherInputs,
        home_l: LineupInputs, away_l: LineupInputs,
        u: UmpireInputs, ctx: GameContext, feats: dict[str, float],
    ) -> tuple[float, float]:
        """Return (top-half-λ, bottom-half-λ).

        Each half-inning λ =
            base_lambda(pitcher)
          × park_factor_1st
          × top_order_factor(opposing top-3 OBP)
          × platoon_factor
          × umpire_factor
          × weather_factor
        """
        kn = self.knobs

        def half(pitcher: PitcherInputs, off: LineupInputs,
                  is_home_pitching: bool) -> float:
            era_blend = feats["home_p_era" if is_home_pitching else "away_p_era"]
            xera = feats["home_p_xera" if is_home_pitching else "away_p_xera"]
            base = (xera / 9.0) * kn.first_inn_era_factor

            # K% / BB% modifiers
            k_mod = 1.0 - kn.k_pct_weight * (pitcher.season_k_pct - 0.225) / 0.05
            bb_mod = 1.0 + kn.bb_pct_weight * (pitcher.season_bb_pct - 0.085) / 0.03
            base *= max(0.5, min(1.5, k_mod * bb_mod))

            # Top-of-order OBP shift (centered at .330)
            top_factor = 1.0 + kn.top_order_weight * (off.top3_obp - 0.330) / 0.330
            # Platoon
            plat_axis = self._platoon_factor(pitcher.hand, off)
            plat_factor = 1.0 - kn.platoon_max_adj * plat_axis  # advantage to pitcher → less offense

            # Umpire
            ump_axis = (u.run_env_idx - 100.0) / 100.0
            ump_factor = 1.0 + kn.ump_weight * ump_axis * (0.5 if self.cfg.enable_abs_2026 else 1.0)

            # Park
            park_factor = ctx.park.park_factor_1st_inn

            # Weather
            wx = 1.0
            if ctx.weather is not None and not is_indoor(ctx.park.code, ctx.roof_open):
                wx *= 1.0 + kn.temp_coeff * (ctx.weather.temperature_f - 70.0)
                axis = wind_orientation_factor(ctx.weather.wind_dir_deg,
                                               ctx.park.orientation_deg)
                wind_signed = axis * ctx.weather.wind_speed_mph
                if wind_signed >= 0:
                    wx *= 1.0 + kn.wind_out_coeff * wind_signed
                else:
                    wx *= 1.0 - kn.wind_in_coeff * (-wind_signed)
                wx *= 1.0 + kn.humidity_coeff * (ctx.weather.humidity_pct - 50.0)

            return max(0.05, base * top_factor * plat_factor * ump_factor * park_factor * wx)

        # Top of 1st: AWAY hitting (vs home pitcher).
        top_half = half(home_p, away_l, is_home_pitching=True)
        # Bottom of 1st: HOME hitting (vs away pitcher).
        bot_half = half(away_p, home_l, is_home_pitching=False)
        return top_half, bot_half


# --- Convenience: serialize features for DuckDB ---------------------------

def features_to_blob(features: Mapping[str, Any]) -> str:
    """Serialise to JSON for the `features.feature_blob` column."""
    return json.dumps({k: _coerce(v) for k, v in features.items()},
                      separators=(",", ":"), sort_keys=True)


def features_from_blob(blob: str) -> dict[str, Any]:
    return json.loads(blob)


def _coerce(v: Any) -> Any:
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def to_dataframe(rows: list[Mapping[str, Any]]) -> pd.DataFrame:
    """Stack a list of feature dicts into a DataFrame, filling NaNs with 0."""
    df = pd.DataFrame(rows)
    return df.fillna(0.0)
