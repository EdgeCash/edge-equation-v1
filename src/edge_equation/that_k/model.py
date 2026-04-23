"""
That K Report -- strikeout projection model.

Translates a pitcher + opposing lineup + game context into a
negative-binomial distribution over total strikeouts in the start.

Base rate
---------
The mean K count for a start is modelled as:

    mean_K = (K_per_BF_baseline) * (expected_BF) * product(adj_i)

where K_per_BF_baseline comes from the pitcher's recent season K/BF
(falling back to an MLB league baseline when history is thin), and
expected_BF is the projected batters faced for the start (league
average ~24, adjusted for the pitcher's IP projection).

Adjustment factors (each a multiplicative modifier around 1.0):

    swstr_adj     -- opposing lineup's aggregate SwStr% relative to
                     MLB average. More whiffs -> more Ks.
    csw_adj       -- called-plus-swinging-strike rate of the lineup,
                     weighted by the matchup's handedness split.
                     CSW% is a stabler leading indicator than raw K%.
    arsenal_adj   -- pitch-specific SwStr% against the expected
                     lineup whiff profile (e.g., high-slider arsenal
                     vs slider-weak lineup -> boost).
    handedness_adj-- pitcher vs lineup L/R composition mismatch
                     multiplier. Top-end lefty vs LHH-heavy lineup
                     skews up; reverse-split platoon downgrades.
    umpire_adj    -- HP umpire K-factor (same 0.93 - 1.08 range
                     the main engine uses). Dome/neutral ump = 1.0.
    weather_adj   -- wind / temp / dome composite. Small effect.
                     Dome -> 1.0; warm + humid slightly depresses
                     K's (ball carries); cold slightly boosts.
    form_adj      -- decay-weighted delta vs season mean over the
                     pitcher's last N starts. Hot pitchers get a
                     modest bump; slumping ones downgrade.

Dispersion
----------
Negative binomial dispersion r governs variance:  var = mean + mean^2/r.
Calibrated to MLB starter K distributions (r ~ 10 produces realistic
stdev of roughly sqrt(mean + mean^2/10) K's per start). Lower r = more
volatility; r is nudged DOWN modestly when the pitcher has thin
recent history so the MC bands widen to reflect uncertainty.

All math here is pure float / Decimal. No RNG -- simulator.py owns
stochastic sampling so this module stays trivially testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple


# MLB league baselines (2023-2025 window).  These are sensible defaults
# when a pitcher / lineup row doesn't ship a measured value.  Two
# numbers a reader can audit:  an average starter posts ~1.06 K/IP,
# a league-average lineup whiffs ~ 11.0% of swings.
LEAGUE_K_PER_BF = 0.235          # strikeouts / batter faced
LEAGUE_SWSTR = 0.110             # lineup aggregate swinging-strike rate
LEAGUE_CSW = 0.290               # called + swinging strike rate
LEAGUE_EXPECTED_BF = 24.0        # average starter BF per outing
LEAGUE_UMP_K_FACTOR = 1.000      # neutral umpire
LEAGUE_TEMPERATURE_F = 72.0      # neutral game-time temp
LEAGUE_WIND_MPH = 5.0            # neutral wind
LEAGUE_NB_DISPERSION = 10.0      # NB r parameter; var = mean + mean^2/r

# Clamp on the total multiplier product -- protects against one bad
# input blowing the projection to absurdity.  The main engine's sanity
# guard caps edges at +30%; K-prop projections shouldn't move more
# than +/- 25% off baseline from any single matchup's factors.
_MIN_TOTAL_ADJ = 0.75
_MAX_TOTAL_ADJ = 1.25

# Arsenal vulnerability weights -- how much each pitch class's SwStr%
# premium is multiplied when the opposing lineup is weak against it.
_ARSENAL_PITCH_WEIGHTS = {
    "FB": 0.9, "FF": 0.9, "SI": 0.9,
    "SL": 1.15, "CU": 1.10, "KC": 1.05,
    "CH": 1.05, "SPL": 1.10, "FC": 1.05,
}


@dataclass(frozen=True)
class PitcherProfile:
    """One starting pitcher's measured inputs for the K model.

    Every field is a real, auditable number the renderer can quote in
    the Read line. Optional fields fall back to league baselines when
    absent (the projector logs a credibility caveat via
    :class:`KReadEvidence` downstream).
    """
    name: str
    team: str
    throws: str = "R"                       # 'L' or 'R'
    k_per_bf: float = LEAGUE_K_PER_BF       # season K/BF
    expected_bf: float = LEAGUE_EXPECTED_BF # projected batters faced
    swstr_pct: Optional[float] = None       # pitcher's own SwStr%
    csw_pct: Optional[float] = None         # pitcher's own CSW%
    arsenal: Dict[str, float] = field(default_factory=dict)  # pitch -> SwStr%
    recent_k_per_bf: Optional[List[Tuple[float, int]]] = None
    # ^ list of (k_per_bf_in_start, age_days) for decay-weighted form.

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "team": self.team,
            "throws": self.throws,
            "k_per_bf": self.k_per_bf,
            "expected_bf": self.expected_bf,
            "swstr_pct": self.swstr_pct,
            "csw_pct": self.csw_pct,
            "arsenal": dict(self.arsenal),
            "recent_k_per_bf": list(self.recent_k_per_bf or []),
        }


@dataclass(frozen=True)
class OpponentLineup:
    """Opposing lineup's aggregate whiff metrics + handedness split."""
    team: str
    swstr_pct: float = LEAGUE_SWSTR         # swings and misses / swings
    csw_pct: float = LEAGUE_CSW             # called+swinging / pitches
    # Share of lineup PAs from left-handed hitters, in [0, 1].
    lhh_share: float = 0.42
    # Per-pitch-class whiff rate (higher = lineup whiffs more often
    # against that pitch).  Used for arsenal_adj.  Missing keys fall
    # back to the lineup's aggregate swstr_pct.
    pitch_whiff: Dict[str, float] = field(default_factory=dict)
    # Optional split: SwStr% vs LHP / RHP separately when available.
    swstr_vs_L: Optional[float] = None
    swstr_vs_R: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "team": self.team,
            "swstr_pct": self.swstr_pct,
            "csw_pct": self.csw_pct,
            "lhh_share": self.lhh_share,
            "pitch_whiff": dict(self.pitch_whiff),
            "swstr_vs_L": self.swstr_vs_L,
            "swstr_vs_R": self.swstr_vs_R,
        }


@dataclass(frozen=True)
class GameContext:
    """Environmental modifiers around a start."""
    dome: bool = False
    temp_f: Optional[float] = None          # game-time temperature
    wind_mph: Optional[float] = None
    wind_dir: Optional[str] = None          # 'in', 'out', 'cross', ...
    umpire_name: Optional[str] = None
    umpire_k_factor: float = LEAGUE_UMP_K_FACTOR  # 1.0 = neutral
    park_k_factor: float = 1.0              # park-level K adjustment

    def to_dict(self) -> dict:
        return {
            "dome": self.dome,
            "temp_f": self.temp_f,
            "wind_mph": self.wind_mph,
            "wind_dir": self.wind_dir,
            "umpire_name": self.umpire_name,
            "umpire_k_factor": self.umpire_k_factor,
            "park_k_factor": self.park_k_factor,
        }


# ------------------------------------------------------------- adjusters

def _swstr_adjustment(lineup: OpponentLineup, pitcher: PitcherProfile) -> float:
    """Lineup SwStr% relative to the league baseline.  Handedness split
    is preferred when available -- e.g., a L-heavy lineup's SwStr vs LHP
    is different from its cross-handed number."""
    swstr = lineup.swstr_pct
    if pitcher.throws == "L" and lineup.swstr_vs_L is not None:
        swstr = lineup.swstr_vs_L
    elif pitcher.throws == "R" and lineup.swstr_vs_R is not None:
        swstr = lineup.swstr_vs_R
    # Multiplier: (lineup - league) * 6 is the slope the model uses.
    # At +0.015 SwStr above league we expect ~+9% K uplift; at -0.015
    # we expect ~-9%.  Calibration derived from season-over-season
    # K/BF vs lineup SwStr% scatter.
    return 1.0 + (swstr - LEAGUE_SWSTR) * 6.0


def _csw_adjustment(lineup: OpponentLineup) -> float:
    """CSW% is a smoother leading indicator than raw K%.  Slope 3.5
    keeps its contribution smaller than swstr_adj's since the two
    signals correlate."""
    return 1.0 + (lineup.csw_pct - LEAGUE_CSW) * 3.5


def _arsenal_adjustment(
    pitcher: PitcherProfile, lineup: OpponentLineup,
) -> float:
    """Match the pitcher's strongest pitch class against the lineup's
    whiff rate vs that class.  Defaults to 1.0 when either side has
    no per-pitch data."""
    if not pitcher.arsenal:
        return 1.0
    lift = 0.0
    weight_sum = 0.0
    for pitch, pitcher_swstr in pitcher.arsenal.items():
        lineup_whiff = lineup.pitch_whiff.get(pitch, lineup.swstr_pct)
        # Uplift is proportional to how MUCH more whiff-friendly the
        # lineup is against this pitch relative to league, weighted by
        # how vulnerable this pitch class is in general.
        class_weight = _ARSENAL_PITCH_WEIGHTS.get(pitch, 1.0)
        lift += class_weight * (pitcher_swstr - LEAGUE_SWSTR) * (
            lineup_whiff - LEAGUE_SWSTR
        )
        weight_sum += class_weight
    if weight_sum == 0:
        return 1.0
    # Scale the composite into a small multiplier around 1.0.
    return 1.0 + max(-0.08, min(0.08, lift / weight_sum * 40.0))


def _handedness_adjustment(
    pitcher: PitcherProfile, lineup: OpponentLineup,
) -> float:
    """L-on-L and R-on-R starts get a small K uplift; cross-handed
    splits pull the multiplier slightly below 1.0."""
    same_side_share = (
        lineup.lhh_share if pitcher.throws == "L" else (1.0 - lineup.lhh_share)
    )
    # Range: 0.0 (fully opposite-handed) -> 1.0 (fully same-handed).
    # Neutral (~0.5) -> 1.0 multiplier.  Each 10pp above 50% -> +1%.
    return 1.0 + (same_side_share - 0.5) * 0.20


def _umpire_adjustment(ctx: GameContext) -> float:
    """HP umpire k_factor is the direct multiplier.  Same semantic as
    the main engine's umpire stash."""
    kf = ctx.umpire_k_factor or LEAGUE_UMP_K_FACTOR
    return max(0.90, min(1.12, float(kf)))


def _weather_adjustment(ctx: GameContext) -> float:
    """Dome -> 1.0.  Open-air: cold temperatures depress ball carry
    and slightly boost K's; warm + humid slightly suppress K rate.
    Wind direction has no documented K effect at MLB scale, so we
    only use it as a tie-breaker for park environment (handled
    elsewhere)."""
    if ctx.dome:
        return 1.0
    temp = ctx.temp_f if ctx.temp_f is not None else LEAGUE_TEMPERATURE_F
    # 1% nudge per 12F delta from neutral (72F).  Capped at +/- 3%.
    temp_delta = (LEAGUE_TEMPERATURE_F - temp) / 12.0 * 0.01
    temp_delta = max(-0.03, min(0.03, temp_delta))
    return 1.0 + temp_delta


def _form_adjustment(pitcher: PitcherProfile) -> float:
    """Decay-weighted last-N-starts K/BF minus the pitcher's season
    K/BF, translated to a modest multiplier.  Half-life = 21 days."""
    recent = pitcher.recent_k_per_bf
    if not recent:
        return 1.0
    half_life = 21.0
    num = 0.0
    den = 0.0
    for value, age_days in recent:
        w = math.exp(-math.log(2) * age_days / half_life)
        num += value * w
        den += w
    if den == 0:
        return 1.0
    recent_avg = num / den
    delta = recent_avg - pitcher.k_per_bf
    # 1% K-mean move per 1pp K/BF delta.  Capped +/- 5%.
    return 1.0 + max(-0.05, min(0.05, delta * 100.0 * 0.01))


# ------------------------------------------------------------- projection

@dataclass(frozen=True)
class KProjectionInputs:
    """All the numbers that went into one projection, stashed so the
    renderer can quote them in the Read line."""
    baseline_mean: float
    swstr_adj: float
    csw_adj: float
    arsenal_adj: float
    handedness_adj: float
    umpire_adj: float
    weather_adj: float
    form_adj: float
    total_adj: float
    projected_mean: float
    nb_dispersion: float
    sample_warning: bool

    def to_dict(self) -> dict:
        return {
            "baseline_mean": self.baseline_mean,
            "swstr_adj": self.swstr_adj,
            "csw_adj": self.csw_adj,
            "arsenal_adj": self.arsenal_adj,
            "handedness_adj": self.handedness_adj,
            "umpire_adj": self.umpire_adj,
            "weather_adj": self.weather_adj,
            "form_adj": self.form_adj,
            "total_adj": self.total_adj,
            "projected_mean": self.projected_mean,
            "nb_dispersion": self.nb_dispersion,
            "sample_warning": self.sample_warning,
        }


def project_strikeouts(
    pitcher: PitcherProfile,
    lineup: OpponentLineup,
    context: GameContext,
) -> KProjectionInputs:
    """Run the full multiplicative adjustment and return a
    KProjectionInputs block -- NO sampling, NO RNG.  The simulator
    consumes (mean, dispersion) out of the returned object.
    """
    baseline = pitcher.k_per_bf * pitcher.expected_bf
    s = _swstr_adjustment(lineup, pitcher)
    c = _csw_adjustment(lineup)
    a = _arsenal_adjustment(pitcher, lineup)
    h = _handedness_adjustment(pitcher, lineup)
    u = _umpire_adjustment(context)
    w = _weather_adjustment(context)
    f = _form_adjustment(pitcher)
    total = s * c * a * h * u * w * f * context.park_k_factor
    total = max(_MIN_TOTAL_ADJ, min(_MAX_TOTAL_ADJ, total))
    projected = baseline * total
    # Widen NB variance when recent history is thin -- fewer measured
    # starts => more uncertainty => lower r => wider MC band.
    r = LEAGUE_NB_DISPERSION
    sample_warning = False
    if not pitcher.recent_k_per_bf or len(pitcher.recent_k_per_bf) < 3:
        r = max(5.0, LEAGUE_NB_DISPERSION * 0.6)
        sample_warning = True
    return KProjectionInputs(
        baseline_mean=baseline,
        swstr_adj=s,
        csw_adj=c,
        arsenal_adj=a,
        handedness_adj=h,
        umpire_adj=u,
        weather_adj=w,
        form_adj=f,
        total_adj=total,
        projected_mean=projected,
        nb_dispersion=r,
        sample_warning=sample_warning,
    )
